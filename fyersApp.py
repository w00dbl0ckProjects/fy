# fyersApp.py (with WebSocket support)
# it is Real Production Ready Code
# many users are using it in production
import os
import json
import threading
import requests
from datetime import datetime, timedelta
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, request, redirect, url_for, session, render_template, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws
import time

# Configuration
REDIRECT_URI = "http://127.0.0.1:5000/callback"
GRANT_TYPE = "authorization_code"
RESPONSE_TYPE = "code"
STATE = "sample_state"

# Fyers instrument master URLs
FYERS_INSTRUMENT_URLS = {
    "NSE_Capital_Market": "https://public.fyers.in/sym_details/NSE_CM_sym_master.json",
    "NSE_Futures_Options": "https://public.fyers.in/sym_details/NSE_FO_sym_master.json",
    "NSE_Commodity": "https://public.fyers.in/sym_details/NSE_COM_sym_master.json",
    "NSE_Commodity_Derivatives": "https://public.fyers.in/sym_details/NSE_CD_sym_master.json",
    "BSE_Capital_Market": "https://public.fyers.in/sym_details/BSE_CM_sym_master.json",
    "BSE_Futures_Options": "https://public.fyers.in/sym_details/BSE_FO_sym_master.json",
    "MCX_Commodity_Derivatives": "https://public.fyers.in/sym_details/MCX_COM_sym_master.json"
}

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(16)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fyers_dashboard.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Initialize SQLAlchemy and SocketIO
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Global variables for WebSocket management
websocket_connections = {}
subscribed_symbols = set()
websocket_thread = None
websocket_lock = threading.Lock()


# Define database models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class FyersCredential(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    client_id = db.Column(db.String(80), nullable=False)
    client_secret = db.Column(db.String(80), nullable=False)
    access_token = db.Column(db.String(256))
    token_expiry = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    user = db.relationship('User', backref=db.backref('fyers_credentials', lazy=True))


class Instrument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fy_token = db.Column(db.String(50), unique=True, nullable=False)
    exchange = db.Column(db.String(20), nullable=False)
    segment = db.Column(db.String(20), nullable=False)
    symbol = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(255))
    expiry_date = db.Column(db.DateTime, nullable=True)
    strike_price = db.Column(db.Float, nullable=True)
    option_type = db.Column(db.String(10), nullable=True)
    lot_size = db.Column(db.Integer, default=1)
    tick_size = db.Column(db.Float, default=0.05)
    isin = db.Column(db.String(50))
    instrument_type = db.Column(db.String(50))
    last_updated = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f"<Instrument {self.symbol}>"


class InstrumentUpdate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exchange = db.Column(db.String(20), nullable=False)
    last_updated = db.Column(db.DateTime, default=datetime.now)
    count = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f"<InstrumentUpdate {self.exchange}>"


class Watchlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    user = db.relationship('User', backref=db.backref('watchlists', lazy=True))
    instruments = db.relationship('WatchlistItem', backref='watchlist', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Watchlist {self.name}>"


class WatchlistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    watchlist_id = db.Column(db.Integer, db.ForeignKey('watchlist.id'), nullable=False)
    instrument_id = db.Column(db.Integer, db.ForeignKey('instrument.id'), nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.now)
    notes = db.Column(db.String(255))

    instrument = db.relationship('Instrument', backref=db.backref('watchlist_items', lazy=True))

    __table_args__ = (
        db.UniqueConstraint('watchlist_id', 'instrument_id', name='uix_watchlist_instrument'),
    )

    def __repr__(self):
        return f"<WatchlistItem {self.id}>"


# Create database tables
with app.app_context():
    db.create_all()


# WebSocket Data Handler
def onmessage(message):
    """Handle incoming WebSocket messages from Fyers"""
    try:
        print(f"Raw WebSocket message: {message}")

        if isinstance(message, dict):
            symbol = message.get('symbol', '')
            if symbol:
                # Process and format the data for candlestick updates
                processed_data = {
                    'symbol': symbol,
                    'ltp': message.get('ltp'),
                    'ch': message.get('ch'),  # Change
                    'chp': message.get('chp'),  # Change percentage
                    'volume': message.get('volume'),
                    'timestamp': message.get('timestamp') or int(time.time() * 1000),
                    'open': message.get('open_price'),
                    'high': message.get('high_price'),
                    'low': message.get('low_price'),
                    'prev_close': message.get('prev_close_price'),
                    # Additional fields for better chart updates
                    'bid': message.get('bid'),
                    'ask': message.get('ask'),
                    'oi': message.get('oi'),  # Open Interest
                    'last_traded_time': message.get('last_traded_time')
                }

                # Emit the data to all connected clients in the symbol room
                socketio.emit('market_data', processed_data, room=f'symbol_{symbol}')
                # Also emit to general market room
                socketio.emit('market_data', processed_data, room='market_updates')

                print(f"WebSocket data for {symbol}: LTP={processed_data.get('ltp', 'N/A')}")
        elif isinstance(message, str):
            # Handle string messages
            try:
                parsed_message = json.loads(message)
                onmessage(parsed_message)
            except json.JSONDecodeError:
                print(f"Could not parse WebSocket message: {message}")
    except Exception as e:
        print(f"Error processing WebSocket message: {str(e)}")


def onerror(message):
    """Handle WebSocket errors"""
    print(f"WebSocket Error: {message}")


def onclose(message):
    """Handle WebSocket close"""
    print(f"WebSocket Connection Closed: {message}")


# WebSocket Management Functions
def start_websocket(access_token):
    """Start WebSocket connection with better error handling"""
    global websocket_thread, websocket_connections

    with websocket_lock:
        if websocket_thread and websocket_thread.is_alive():
            return True

        try:
            # Initialize the data WebSocket
            fyers_ws = data_ws.FyersDataSocket(
                access_token=access_token,
                log_path="",
                litemode=False,
                write_to_file=False,
                on_message=onmessage,
                on_error=onerror,
                on_close=onclose,
            )

            websocket_connections['main'] = fyers_ws

            # Start WebSocket in a separate thread
            def run_websocket():
                try:
                    print("Starting Fyers WebSocket connection...")
                    fyers_ws.connect()
                except Exception as e:
                    print(f"WebSocket connection error: {str(e)}")

            websocket_thread = threading.Thread(target=run_websocket, daemon=True)
            websocket_thread.start()

            # Give it some time to connect
            time.sleep(2)

            return True
        except Exception as e:
            print(f"Error starting WebSocket: {str(e)}")
            return False


def subscribe_symbol(symbol):
    """Subscribe to a symbol for live data with better error handling and data format"""
    global websocket_connections, subscribed_symbols

    try:
        if 'main' in websocket_connections:
            fyers_ws = websocket_connections['main']

            # Clean the symbol for subscription
            clean_sym = format_symbol_for_fyers(symbol)

            # Subscribe to comprehensive data feed
            symbols = [clean_sym]
            print(f"Attempting to subscribe to symbol: {clean_sym}")

            # Subscribe to multiple data types for comprehensive updates
            result = fyers_ws.subscribe(symbols=symbols, data_type="SymbolUpdate")

            if result:
                subscribed_symbols.add(clean_sym)
                print(f"Successfully subscribed to {clean_sym}")
                return True
            else:
                print(f"Failed to subscribe to {clean_sym}")
                return False
    except Exception as e:
        print(f"Error subscribing to {symbol}: {str(e)}")

    return False


def unsubscribe_symbol(symbol):
    """Unsubscribe from a symbol"""
    global websocket_connections, subscribed_symbols

    try:
        if 'main' in websocket_connections:
            fyers_ws = websocket_connections['main']

            # Unsubscribe from live data feed
            symbols = [symbol]
            fyers_ws.unsubscribe(symbols=symbols, data_type="SymbolUpdate")

            subscribed_symbols.discard(symbol)
            print(f"Unsubscribed from {symbol}")
            return True
    except Exception as e:
        print(f"Error unsubscribing from {symbol}: {str(e)}")

    return False


# SocketIO Event Handlers
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    client_id = request.sid
    print(f"Client connected: {client_id}")

    # Send connection confirmation
    emit('status', {
        'message': 'Connected to live market data server',
        'client_id': client_id,
        'timestamp': int(time.time() * 1000)
    })


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    client_id = request.sid
    print(f"Client disconnected: {client_id}")


@socketio.on('subscribe')
def handle_subscribe(data):
    """Handle symbol subscription request"""
    symbol = data.get('symbol')
    client_id = request.sid

    if symbol:
        print(f"Client {client_id} subscribing to {symbol}")

        # Join the symbol room
        join_room(f'symbol_{symbol}')
        join_room('market_updates')

        # Subscribe to WebSocket if not already subscribed
        if symbol not in subscribed_symbols:
            # Get any valid access token for WebSocket
            creds = FyersCredential.query.filter(
                FyersCredential.access_token.isnot(None),
                FyersCredential.token_expiry > datetime.now()
            ).first()

            if creds:
                if 'main' not in websocket_connections:
                    success = start_websocket(creds.access_token)
                    if not success:
                        emit('error', {'message': 'Failed to start WebSocket connection'})
                        return

                # Wait a moment for WebSocket to be ready
                time.sleep(1)

                success = subscribe_symbol(symbol)
                if success:
                    emit('subscribed', {'symbol': symbol, 'status': 'success'})
                else:
                    emit('error', {'message': f'Failed to subscribe to {symbol}'})
            else:
                emit('error', {'message': 'No valid Fyers credentials found'})
        else:
            emit('subscribed', {'symbol': symbol, 'status': 'already_subscribed'})

        print(f"Client {client_id} subscribed to {symbol}")


@socketio.on('unsubscribe')
def handle_unsubscribe(data):
    """Handle symbol unsubscription request"""
    symbol = data.get('symbol')
    client_id = request.sid

    if symbol:
        print(f"Client {client_id} unsubscribing from {symbol}")

        # Leave the symbol room
        leave_room(f'symbol_{symbol}')

        # Check if any other clients are still subscribed to this symbol
        try:
            room_clients = socketio.server.manager.get_participants('/', f'symbol_{symbol}')
            if len(room_clients) == 0:
                unsubscribe_symbol(symbol)
                print(f"No more clients for {symbol}, unsubscribed from WebSocket")
        except:
            # Fallback: just unsubscribe
            unsubscribe_symbol(symbol)

        emit('unsubscribed', {'symbol': symbol})
        print(f"Client {client_id} unsubscribed from {symbol}")


@socketio.on('get_historical_data')
def handle_get_historical_data(data):
    """Handle request for historical data"""
    symbol = data.get('symbol')
    timeframe = data.get('timeframe', '1')
    interval = data.get('interval', '5')

    # Get user credentials
    user_id = session.get('user_id')
    if not user_id:
        emit('error', {'message': 'User not authenticated'})
        return

    fyers_client = get_fyers_client(user_id)
    if not fyers_client:
        emit('error', {'message': 'Fyers client not available'})
        return

    try:
        # Get historical data (reuse the existing logic)
        from datetime import datetime, timedelta
        import pytz

        ist = pytz.timezone('Asia/Kolkata')
        to_date = datetime.now(ist)
        timeframe_days = int(timeframe)
        from_date = to_date - timedelta(days=timeframe_days)

        from_date_str = from_date.strftime('%Y-%m-%d')
        to_date_str = to_date.strftime('%Y-%m-%d')

        interval_mapping = {
            '1': '1',
            '5': '5',
            '15': '15',
            '60': '60',
            'D': 'D'
        }

        fyers_interval = interval_mapping.get(interval, '5')

        historical_data = {
            "symbol": clean_symbol(symbol),
            "resolution": fyers_interval,
            "date_format": "1",
            "range_from": from_date_str,
            "range_to": to_date_str,
            "cont_flag": "1"
        }

        response = fyers_client.history(historical_data)

        if response and response.get('s') == 'ok' and 'candles' in response:
            candles = response['candles']
            processed_data = []

            for candle in candles:
                if len(candle) >= 6:
                    timestamp = int(candle[0])
                    dt = datetime.fromtimestamp(timestamp, tz=ist)

                    processed_data.append({
                        'datetime': dt.isoformat(),
                        'timestamp': timestamp * 1000,
                        'open': float(candle[1]),
                        'high': float(candle[2]),
                        'low': float(candle[3]),
                        'close': float(candle[4]),
                        'volume': int(candle[5])
                    })

            emit('historical_data', {
                'success': True,
                'data': processed_data,
                'symbol': symbol,
                'timeframe': timeframe,
                'interval': interval,
                'count': len(processed_data)
            })
        else:
            emit('historical_data', {
                'success': True,
                'data': [],
                'symbol': symbol,
                'message': 'No historical data available'
            })

    except Exception as e:
        print(f"Error fetching historical data: {str(e)}")
        emit('error', {'message': f'Error fetching historical data: {str(e)}'})


# Add a heartbeat mechanism to keep WebSocket connections alive
def start_heartbeat():
    """Start heartbeat to keep connections alive"""

    def heartbeat():
        while True:
            try:
                time.sleep(30)  # Send heartbeat every 30 seconds
                socketio.emit('heartbeat', {
                    'timestamp': int(time.time() * 1000),
                    'subscribed_symbols': list(subscribed_symbols)
                }, room='market_updates')
            except Exception as e:
                print(f"Heartbeat error: {str(e)}")

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()


# Helper functions
def get_fyers_client(user_id):
    """Get a Fyers client for the given user ID with token validation"""
    is_valid, message = validate_and_refresh_token(user_id)

    if not is_valid:
        print(f"Token validation failed for user {user_id}: {message}")
        return None

    creds = FyersCredential.query.filter_by(user_id=user_id).first()

    try:
        return fyersModel.FyersModel(
            token=creds.access_token,
            is_async=False,
            client_id=creds.client_id,
            log_path=""
        )
    except Exception as e:
        print(f"Error creating Fyers client: {str(e)}")
        return None


def save_credentials(user_id, client_id, client_secret, access_token):
    """Save or update Fyers credentials for a user"""
    # Token expiry is set to 1 day from now (Fyers tokens usually expire in 1 day)
    token_expiry = datetime.now() + timedelta(days=1)

    creds = FyersCredential.query.filter_by(user_id=user_id).first()
    if creds:
        creds.client_id = client_id
        creds.client_secret = client_secret
        creds.access_token = access_token
        creds.token_expiry = token_expiry
        creds.updated_at = datetime.now()
    else:
        creds = FyersCredential(
            user_id=user_id,
            client_id=client_id,
            client_secret=client_secret,
            access_token=access_token,
            token_expiry=token_expiry
        )
        db.session.add(creds)

    db.session.commit()


def login_required(func):
    """Decorator to require login for views"""

    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page', 'warning')
            return redirect(url_for('login'))
        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper


def validate_and_refresh_token(user_id):
    """Validate token and refresh if needed"""
    creds = FyersCredential.query.filter_by(user_id=user_id).first()

    if not creds:
        return False, "No credentials found"

    # Check if token exists and is not expired
    if not creds.access_token or (creds.token_expiry and creds.token_expiry < datetime.now()):
        return False, "Token expired or missing"

    # Test the token with a simple API call
    try:
        fyers_client = fyersModel.FyersModel(
            token=creds.access_token,
            is_async=False,
            client_id=creds.client_id,
            log_path=""
        )

        # Test with profile call
        response = fyers_client.get_profile()
        if response and response.get('s') == 'ok':
            return True, "Token valid"
        else:
            return False, "Token invalid"
    except Exception as e:
        return False, f"Token validation failed: {str(e)}"


def fetch_instruments(exchange_code):
    """Fetch instrument data from Fyers API"""
    try:
        if exchange_code not in FYERS_INSTRUMENT_URLS:
            return None, f"Invalid exchange code: {exchange_code}"

        url = FYERS_INSTRUMENT_URLS[exchange_code]
        response = requests.get(url, timeout=30)

        if response.status_code != 200:
            return None, f"Failed to fetch data: HTTP {response.status_code}"

        data = response.json()
        return data, None
    except Exception as e:
        return None, str(e)


def update_instruments(exchange_code):
    """Update instruments for a specific exchange"""
    data, error = fetch_instruments(exchange_code)

    if error:
        return 0, error

    if not data:
        return 0, "No data received"

    # Get exchange and segment from exchange_code
    parts = exchange_code.split('_')
    exchange = parts[0]
    segment = parts[1] if len(parts) > 1 else None

    count = 0
    try:
        # Process instruments in batches to avoid memory issues
        batch_size = 1000
        instrument_batch = []

        for symbol, details in data.items():
            # Check if instrument already exists
            existing = Instrument.query.filter_by(fy_token=details.get('fyToken')).first()

            expiry_date = None
            if details.get('expiryDate'):
                try:
                    # Convert timestamp to datetime
                    expiry_date = datetime.fromtimestamp(int(details.get('expiryDate')) / 1000)
                except:
                    pass

            if existing:
                # Update existing instrument
                existing.symbol = symbol
                existing.name = details.get('symDetails', '')
                existing.exchange = exchange
                existing.segment = segment
                existing.expiry_date = expiry_date
                existing.strike_price = details.get('strikePrice')
                existing.option_type = details.get('optType')
                existing.lot_size = details.get('minLotSize', 1)
                existing.tick_size = details.get('tickSize', 0.05)
                existing.isin = details.get('isin', '')
                existing.instrument_type = details.get('exInstType', '')
                existing.last_updated = datetime.now()
            else:
                # Create new instrument
                instrument = Instrument(
                    fy_token=details.get('fyToken'),
                    symbol=symbol,
                    name=details.get('symDetails', ''),
                    exchange=exchange,
                    segment=segment,
                    expiry_date=expiry_date,
                    strike_price=details.get('strikePrice'),
                    option_type=details.get('optType'),
                    lot_size=details.get('minLotSize', 1),
                    tick_size=details.get('tickSize', 0.05),
                    isin=details.get('isin', ''),
                    instrument_type=details.get('exInstType', '')
                )
                instrument_batch.append(instrument)

            count += 1

            # Commit in batches
            if len(instrument_batch) >= batch_size:
                db.session.add_all(instrument_batch)
                db.session.commit()
                instrument_batch = []

        # Add any remaining instruments
        if instrument_batch:
            db.session.add_all(instrument_batch)
            db.session.commit()

        # Update last update timestamp
        update_record = InstrumentUpdate.query.filter_by(exchange=exchange_code).first()
        if update_record:
            update_record.last_updated = datetime.now()
            update_record.count = count
        else:
            update_record = InstrumentUpdate(
                exchange=exchange_code,
                last_updated=datetime.now(),
                count=count
            )
            db.session.add(update_record)

        db.session.commit()
        return count, None
    except Exception as e:
        db.session.rollback()
        return 0, str(e)


@app.route('/check-token-status')
@login_required
def check_token_status():
    user_id = session.get('user_id')
    is_valid, message = validate_and_refresh_token(user_id)

    creds = FyersCredential.query.filter_by(user_id=user_id).first()

    status = {
        'valid': is_valid,
        'message': message,
        'has_credentials': bool(creds and creds.client_id and creds.client_secret),
        'has_token': bool(creds and creds.access_token),
        'token_expiry': creds.token_expiry.isoformat() if creds and creds.token_expiry else None
    }

    return jsonify(status)


@app.route('/instruments')
@login_required
def instruments():
    # Get list of exchanges with update status
    updates = InstrumentUpdate.query.all()

    # Convert to dictionary for easier access
    exchange_updates = {update.exchange: update for update in updates}

    # Prepare data for the template
    exchanges_data = []
    for code, url in FYERS_INSTRUMENT_URLS.items():
        update = exchange_updates.get(code)
        exchanges_data.append({
            'code': code,
            'name': code.replace('_', ' '),
            'last_updated': update.last_updated if update else None,
            'count': update.count if update else 0
        })

    return render_template('instruments.html', exchanges=exchanges_data)


@app.route('/instruments/update/<exchange_code>')
@login_required
def update_exchange_instruments(exchange_code):
    count, error = update_instruments(exchange_code)

    if error:
        flash(f"Error updating {exchange_code}: {error}", 'danger')
    else:
        flash(f"Successfully updated {count} instruments for {exchange_code}", 'success')

    return redirect(url_for('instruments'))


@app.route('/instruments/browse/<exchange_code>')
@login_required
def browse_instruments(exchange_code):
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 100, type=int)
    search = request.args.get('search', '')

    # Split exchange code to get exchange and segment
    parts = exchange_code.split('_')
    exchange = parts[0]
    segment = parts[1] if len(parts) > 1 else None

    # Build query
    query = Instrument.query.filter_by(exchange=exchange)
    if segment:
        query = query.filter_by(segment=segment)

    # Apply search filter if provided
    if search:
        query = query.filter(
            (Instrument.symbol.contains(search)) |
            (Instrument.name.contains(search))
        )

    # Get paginated results
    instruments = query.order_by(Instrument.symbol).paginate(page=page, per_page=per_page)

    return render_template(
        'browse_instruments.html',
        instruments=instruments,
        exchange_code=exchange_code,
        search=search
    )


@app.route('/instruments/view/<fy_token>')
@login_required
def view_instrument(fy_token):
    instrument = Instrument.query.filter_by(fy_token=fy_token).first_or_404()

    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    market_data = None
    if fyers_client:
        try:
            symbol = f"{instrument.exchange}:{instrument.symbol}"
            response = fyers_client.quotes({"symbols": symbol})

            # Debug the response structure
            print(f"Market data response for {symbol}:", response)

            if response and 'd' in response:
                if isinstance(response['d'], list) and len(response['d']) > 0:
                    market_data = response['d'][0]
                elif isinstance(response['d'], dict):
                    # Some responses might be structured differently
                    market_data = response['d']
        except Exception as e:
            print(f"Error fetching market data: {str(e)}")
            flash(f"Error fetching market data: {str(e)}", "warning")

    return render_template('view_instrument.html', instrument=instrument, market_data=market_data)


@app.route('/api/search_instruments')
@login_required
def search_instruments():
    search = request.args.get('q', '')
    limit = request.args.get('limit', 20, type=int)

    # Cap the maximum limit to prevent performance issues
    limit = min(limit, 100)

    if len(search) < 2:
        return jsonify([])

    # Use case-insensitive search with LIKE
    instruments = Instrument.query.filter(
        (Instrument.symbol.ilike(f'%{search}%')) |
        (Instrument.name.ilike(f'%{search}%'))
    ).order_by(Instrument.symbol).limit(limit).all()

    results = []
    for instrument in instruments:
        results.append({
            'fy_token': instrument.fy_token,
            'symbol': instrument.symbol,
            'exchange': instrument.exchange,
            'name': instrument.name,
            'display': f"{instrument.symbol} - {instrument.name} ({instrument.exchange})"
        })

    return jsonify(results)


@app.route('/watchlists')
@login_required
def watchlists():
    user_id = session.get('user_id')
    user_watchlists = Watchlist.query.filter_by(user_id=user_id).all()

    return render_template('watchlists.html', watchlists=user_watchlists)


@app.route('/watchlists/create', methods=['GET', 'POST'])
@login_required
def create_watchlist():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description', '')

        if not name:
            flash('Watchlist name is required', 'danger')
            return render_template('create_watchlist.html')

        user_id = session.get('user_id')
        watchlist = Watchlist(user_id=user_id, name=name, description=description)
        db.session.add(watchlist)
        db.session.commit()

        flash('Watchlist created successfully', 'success')
        return redirect(url_for('watchlist_detail', watchlist_id=watchlist.id))

    return render_template('create_watchlist.html')


@app.route('/watchlists/<int:watchlist_id>')
@login_required
def watchlist_detail(watchlist_id):
    user_id = session.get('user_id')
    watchlist = Watchlist.query.filter_by(id=watchlist_id, user_id=user_id).first_or_404()

    # Get the market data for instruments in the watchlist
    watchlist_items = WatchlistItem.query.filter_by(watchlist_id=watchlist.id).all()
    items_with_data = []

    fyers_client = get_fyers_client(user_id)
    if fyers_client:
        for item in watchlist_items:
            instrument = item.instrument
            if instrument:  # Check if instrument exists
                symbol = f"{instrument.exchange}:{instrument.symbol}"

                try:
                    response = fyers_client.quotes({"symbols": symbol})
                    market_data = None

                    if response and 'd' in response:
                        if isinstance(response['d'], list) and len(response['d']) > 0:
                            market_data = response['d'][0]
                        elif isinstance(response['d'], dict):
                            market_data = response['d']

                    items_with_data.append({
                        'item': item,
                        'instrument': instrument,
                        'market_data': market_data
                    })
                except Exception as e:
                    print(f"Error fetching market data for {symbol}: {str(e)}")
                    items_with_data.append({
                        'item': item,
                        'instrument': instrument,
                        'market_data': None
                    })
            else:
                print(f"Warning: Watchlist item {item.id} has missing instrument")
                # Add a placeholder or skip this item
                items_with_data.append({
                    'item': item,
                    'instrument': None,
                    'market_data': None
                })
    else:
        # If no Fyers client, just add the instruments without market data
        for item in watchlist_items:
            instrument = item.instrument
            items_with_data.append({
                'item': item,
                'instrument': instrument,
                'market_data': None
            })

    return render_template('watchlist_detail.html', watchlist=watchlist, items=items_with_data)


@app.route('/watchlists/<int:watchlist_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_watchlist(watchlist_id):
    user_id = session.get('user_id')
    watchlist = Watchlist.query.filter_by(id=watchlist_id, user_id=user_id).first_or_404()

    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description', '')

        if not name:
            flash('Watchlist name is required', 'danger')
            return render_template('edit_watchlist.html', watchlist=watchlist)

        watchlist.name = name
        watchlist.description = description
        watchlist.updated_at = datetime.now()
        db.session.commit()

        flash('Watchlist updated successfully', 'success')
        return redirect(url_for('watchlist_detail', watchlist_id=watchlist.id))

    return render_template('edit_watchlist.html', watchlist=watchlist)


@app.route('/watchlists/<int:watchlist_id>/delete', methods=['POST'])
@login_required
def delete_watchlist(watchlist_id):
    user_id = session.get('user_id')
    watchlist = Watchlist.query.filter_by(id=watchlist_id, user_id=user_id).first_or_404()

    db.session.delete(watchlist)
    db.session.commit()

    flash('Watchlist deleted successfully', 'success')
    return redirect(url_for('watchlists'))


@app.route('/watchlists/<int:watchlist_id>/add', methods=['GET', 'POST'])
@login_required
def add_to_watchlist(watchlist_id):
    user_id = session.get('user_id')
    watchlist = Watchlist.query.filter_by(id=watchlist_id, user_id=user_id).first_or_404()

    if request.method == 'POST':
        instrument_id = request.form.get('instrument_id')
        notes = request.form.get('notes', '')

        if not instrument_id:
            flash('No instrument selected', 'danger')
            return redirect(url_for('add_to_watchlist', watchlist_id=watchlist.id))

        # Verify the instrument exists
        instrument = Instrument.query.filter_by(fy_token=instrument_id).first()
        if not instrument:
            flash('Selected instrument not found', 'danger')
            return redirect(url_for('add_to_watchlist', watchlist_id=watchlist.id))

        # Check if the instrument is already in the watchlist
        existing = WatchlistItem.query.filter_by(
            watchlist_id=watchlist.id,
            instrument_id=instrument.id
        ).first()

        if existing:
            flash('This instrument is already in your watchlist', 'warning')
        else:
            item = WatchlistItem(
                watchlist_id=watchlist.id,
                instrument_id=instrument.id,
                notes=notes
            )
            db.session.add(item)
            db.session.commit()
            flash('Instrument added to watchlist', 'success')

        return redirect(url_for('watchlist_detail', watchlist_id=watchlist.id))

    return render_template('add_to_watchlist.html', watchlist=watchlist)


@app.route('/watchlists/<int:watchlist_id>/remove/<int:item_id>', methods=['POST'])
@login_required
def remove_from_watchlist(watchlist_id, item_id):
    user_id = session.get('user_id')
    watchlist = Watchlist.query.filter_by(id=watchlist_id, user_id=user_id).first_or_404()
    item = WatchlistItem.query.filter_by(id=item_id, watchlist_id=watchlist.id).first_or_404()

    db.session.delete(item)
    db.session.commit()

    flash('Instrument removed from watchlist', 'success')
    return redirect(url_for('watchlist_detail', watchlist_id=watchlist.id))


@app.route('/api/user_watchlists')
@login_required
def api_user_watchlists():
    user_id = session.get('user_id')
    watchlists = Watchlist.query.filter_by(user_id=user_id).all()

    result = []
    for watchlist in watchlists:
        item_count = WatchlistItem.query.filter_by(watchlist_id=watchlist.id).count()
        result.append({
            'id': watchlist.id,
            'name': watchlist.name,
            'item_count': item_count
        })

    return jsonify(result)


@app.route('/api/add_to_watchlist', methods=['POST'])
@login_required
def api_add_to_watchlist():
    user_id = session.get('user_id')
    data = request.json

    watchlist_id = data.get('watchlist_id')
    instrument_id = data.get('instrument_id')
    notes = data.get('notes', '')

    if not watchlist_id or not instrument_id:
        return jsonify({'success': False, 'message': 'Missing required parameters'}), 400

    # Verify the watchlist belongs to the user
    watchlist = Watchlist.query.filter_by(id=watchlist_id, user_id=user_id).first()
    if not watchlist:
        return jsonify({'success': False, 'message': 'Watchlist not found'}), 404

    # Check if the instrument is already in the watchlist
    existing = WatchlistItem.query.filter_by(
        watchlist_id=watchlist.id,
        instrument_id=instrument_id
    ).first()

    if existing:
        return jsonify({'success': False, 'message': 'Instrument already in watchlist'}), 409

    try:
        item = WatchlistItem(
            watchlist_id=watchlist.id,
            instrument_id=instrument_id,
            notes=notes
        )
        db.session.add(item)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Instrument added to watchlist'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


# Routes
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        # Basic validation
        if not all([username, email, password, confirm_password]):
            flash('All fields are required', 'danger')
            return render_template('register.html')

        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return render_template('register.html')

        # Check if user exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return render_template('register.html')

        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return render_template('register.html')

        # Create new user
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('Please enter both username and password', 'danger')
            return render_template('login.html')

        user = User.query.filter_by(username=username).first()

        if not user or not user.check_password(password):
            flash('Invalid username or password', 'danger')
            return render_template('login.html')

        session.permanent = True
        session['user_id'] = user.id
        session['username'] = user.username

        flash(f'Welcome back, {user.username}!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('index'))


@app.route('/debug-token')
@login_required
def debug_token():
    user_id = session.get('user_id')
    creds = FyersCredential.query.filter_by(user_id=user_id).first()

    debug_info = {
        'user_id': user_id,
        'has_credentials': bool(creds),
        'client_id': creds.client_id if creds else None,
        'has_access_token': bool(creds and creds.access_token),
        'token_length': len(creds.access_token) if creds and creds.access_token else 0,
        'token_expiry': creds.token_expiry.isoformat() if creds and creds.token_expiry else None,
        'token_expired': bool(creds and creds.token_expiry and creds.token_expiry < datetime.now()),
        'current_time': datetime.now().isoformat()
    }

    return jsonify(debug_info)


@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session.get('user_id')

    # Check token status first
    is_valid, message = validate_and_refresh_token(user_id)

    if not is_valid:
        creds = FyersCredential.query.filter_by(user_id=user_id).first()
        if not creds or not creds.client_id or not creds.client_secret:
            flash('Please add your Fyers API credentials first', 'warning')
            return redirect(url_for('manage_credentials'))
        else:
            flash(f'Token issue: {message}. Please reconnect your Fyers account.', 'warning')
            return redirect(url_for('connect_fyers'))

    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        flash('Unable to connect to Fyers. Please try reconnecting.', 'warning')
        return redirect(url_for('connect_fyers'))

    try:
        profile = fyers_client.get_profile()

        # Only proceed if profile call is successful
        if not profile or profile.get('s') != 'ok':
            flash('Failed to fetch profile. Please reconnect your Fyers account.', 'warning')
            return redirect(url_for('connect_fyers'))

        funds = fyers_client.funds()
        holdings = fyers_client.holdings()

        return render_template('dashboard.html',
                               profile=profile,
                               funds=funds,
                               holdings=holdings,
                               current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        flash(f'Error fetching data: {str(e)}', 'danger')
        return redirect(url_for('connect_fyers'))


@app.route('/connect-fyers')
@login_required
def connect_fyers():
    user_id = session.get('user_id')

    # Get existing credentials if any
    creds = FyersCredential.query.filter_by(user_id=user_id).first()

    if not creds or not creds.client_id or not creds.client_secret:
        # If no credentials, redirect to manage credentials page
        flash('Please add your Fyers API credentials first', 'warning')
        return redirect(url_for('manage_credentials'))

    # Store in session temporarily
    session['fyers_client_id'] = creds.client_id
    session['fyers_client_secret'] = creds.client_secret

    # Generate auth URL
    try:
        fyers_session = fyersModel.SessionModel(
            client_id=creds.client_id,
            redirect_uri=REDIRECT_URI,
            response_type=RESPONSE_TYPE,
            state=STATE,
            secret_key=creds.client_secret,
            grant_type=GRANT_TYPE
        )
        auth_url = fyers_session.generate_authcode()
        return redirect(auth_url)
    except Exception as e:
        flash(f"Error generating authorization URL: {str(e)}", 'danger')
        return redirect(url_for('account'))


@app.route('/manage-credentials', methods=['GET', 'POST'])
@login_required
def manage_credentials():
    user_id = session.get('user_id')
    creds = FyersCredential.query.filter_by(user_id=user_id).first()

    if request.method == 'POST':
        client_id = request.form.get('client_id')
        client_secret = request.form.get('client_secret')

        if not client_id or not client_secret:
            flash('Please provide both Client ID and Secret Key', 'danger')
            return render_template('manage_credentials.html', credentials=creds)

        if creds:
            creds.client_id = client_id
            creds.client_secret = client_secret
            creds.access_token = None  # Clear existing token
            creds.token_expiry = None
            creds.updated_at = datetime.now()
        else:
            creds = FyersCredential(
                user_id=user_id,
                client_id=client_id,
                client_secret=client_secret
            )
            db.session.add(creds)

        db.session.commit()
        flash('Fyers API credentials updated successfully', 'success')
        return redirect(url_for('connect_fyers'))

    return render_template('manage_credentials.html', credentials=creds)


@app.route('/callback')
@login_required
def callback():
    if 'error' in request.args:
        flash(f"Authorization failed: {request.args.get('error_description')}", 'danger')
        return redirect(url_for('connect_fyers'))

    auth_code = request.args.get('auth_code')
    if not auth_code:
        flash("Authorization code not received", 'danger')
        return redirect(url_for('connect_fyers'))

    client_id = session.get('fyers_client_id')
    client_secret = session.get('fyers_client_secret')

    if not client_id or not client_secret:
        flash("Client information missing, please try again", 'danger')
        return redirect(url_for('connect_fyers'))

    try:
        fyers_session = fyersModel.SessionModel(
            client_id=client_id,
            redirect_uri=REDIRECT_URI,
            response_type=RESPONSE_TYPE,
            state=STATE,
            secret_key=client_secret,
            grant_type=GRANT_TYPE
        )
        fyers_session.set_token(auth_code)
        response = fyers_session.generate_token()

        if 'access_token' in response:
            access_token = response['access_token']
            user_id = session.get('user_id')

            # Save credentials to database
            save_credentials(user_id, client_id, client_secret, access_token)

            # Clear temporary session data
            session.pop('fyers_client_id', None)
            session.pop('fyers_client_secret', None)

            flash('Fyers account connected successfully!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash(f"Failed to get access token: {response}", 'danger')
            return redirect(url_for('connect_fyers'))
    except Exception as e:
        flash(f"Error generating token: {str(e)}", 'danger')
        return redirect(url_for('connect_fyers'))


@app.route('/orders')
@login_required
def orders():
    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        flash('Please connect your Fyers account first', 'warning')
        return redirect(url_for('connect_fyers'))

    try:
        orders_data = fyers_client.orderbook()
        return render_template('orders.html', orders=orders_data)
    except Exception as e:
        flash(f'Error fetching orders: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))


@app.route('/positions')
@login_required
def positions():
    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        flash('Please connect your Fyers account first', 'warning')
        return redirect(url_for('connect_fyers'))

    try:
        positions_data = fyers_client.positions()
        return render_template('positions.html', positions=positions_data)
    except Exception as e:
        flash(f'Error fetching positions: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))


def clean_symbol(symbol: str) -> str:
    """Clean and format symbol properly"""
    if not symbol:
        return symbol

    # Remove any duplicate exchange prefixes
    parts = symbol.split(':')
    if len(parts) >= 3 and parts[0] == parts[1]:
        # If we have NSE:NSE:SYMBOL, convert to NSE:SYMBOL
        return f"{parts[0]}:{':'.join(parts[2:])}"
    elif len(parts) == 2:
        # Already properly formatted
        return symbol
    else:
        # Single symbol without exchange, add default NSE
        return f"NSE:{symbol}"


def format_symbol_for_fyers(symbol: str) -> str:
    """Format symbol specifically for Fyers API calls"""
    cleaned = clean_symbol(symbol)

    # Ensure we don't have double exchange prefixes
    if cleaned.count(':') > 1:
        parts = cleaned.split(':')
        return f"{parts[0]}:{parts[-1]}"

    return cleaned


# market route with live option
@app.route('/market')
@login_required
def market():
    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        flash('Please connect your Fyers account first', 'warning')
        return redirect(url_for('connect_fyers'))

    # Check if live chart is requested
    is_live = request.args.get('live') == 'true'
    if is_live:
        return redirect(url_for('market_live', **request.args))

    # Get symbol from query parameters or use default
    fy_token = request.args.get('fy_token')
    symbol = request.args.get('symbol', 'NSE:NIFTY50-INDEX')

    # If fy_token is provided, get the instrument and symbol
    instrument = None
    if fy_token:
        instrument = Instrument.query.filter_by(fy_token=fy_token).first()
        if instrument:
            symbol = f"{instrument.exchange}:{instrument.symbol}"
            symbol = clean_symbol(symbol)

    try:
        response = fyers_client.quotes({"symbols": symbol})

        # Format the data for the template
        formatted_data = None
        if response and 'd' in response:
            if isinstance(response['d'], list) and len(response['d']) > 0:
                formatted_data = response['d'][0]
            elif isinstance(response['d'], dict):
                formatted_data = response['d']

        if not formatted_data:
            flash('No data found for the symbol: ' + symbol, 'warning')

        # Get some popular instruments for quick selection
        popular_instruments = Instrument.query.filter(
            Instrument.symbol.in_(['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK'])
        ).all()

        return render_template(
            'market.html',
            market_data=formatted_data,
            symbol=symbol,
            instrument=instrument,
            popular_instruments=popular_instruments
        )
    except Exception as e:
        flash(f'Error fetching market data: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))


# route for the live market view
# In fyersApp.py, update the market_live route
@app.route('/market/live')
@login_required
def market_live():
    """Live market data view with real-time charts"""
    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        flash('Please connect your Fyers account first', 'warning')
        return redirect(url_for('connect_fyers'))

    # Get symbol from query parameters or use default
    fy_token = request.args.get('fy_token')
    symbol = request.args.get('symbol', 'NSE:RELIANCE-EQ')  # Changed default

    # If fy_token is provided, get the instrument and symbol
    instrument = None
    if fy_token:
        instrument = Instrument.query.filter_by(fy_token=fy_token).first()
        if instrument:
            symbol = f"{instrument.exchange}:{instrument.symbol}"
            symbol = clean_symbol(symbol)

    # Initialize WebSocket if not already started
    creds = FyersCredential.query.filter_by(user_id=user_id).first()
    if creds and creds.access_token:
        if 'main' not in websocket_connections:
            start_websocket(creds.access_token)

    return render_template(
        'market_live.html',
        symbol=symbol,
        instrument=instrument
    )


# route for real-time data testing
@app.route('/test_websocket')
@login_required
def test_websocket():
    """Test WebSocket connection"""
    user_id = session.get('user_id')
    creds = FyersCredential.query.filter_by(user_id=user_id).first()

    if creds and creds.access_token:
        success = start_websocket(creds.access_token)
        if success:
            # Test subscription
            test_symbol = "NSE:NIFTY50-INDEX"
            sub_success = subscribe_symbol(test_symbol)
            return jsonify({
                'websocket_started': success,
                'test_subscription': sub_success,
                'symbol': test_symbol,
                'subscribed_symbols': list(subscribed_symbols)
            })
        else:
            return jsonify({'error': 'Failed to start WebSocket'}), 500
    else:
        return jsonify({'error': 'No valid credentials found'}), 400


# route for live watchlist
@app.route('/watchlist-live/<int:watchlist_id>')
@login_required
def watchlist_live(watchlist_id):
    user_id = session.get('user_id')
    watchlist = Watchlist.query.filter_by(id=watchlist_id, user_id=user_id).first_or_404()

    # Get initial data for instruments in the watchlist
    watchlist_items = WatchlistItem.query.filter_by(watchlist_id=watchlist.id).all()

    # Initialize WebSocket if not already started
    creds = FyersCredential.query.filter_by(user_id=user_id).first()
    if creds and creds.access_token:
        if 'main' not in websocket_connections:
            start_websocket(creds.access_token)

    return render_template('watchlist_live.html', watchlist=watchlist, items=watchlist_items)


# API endpoint to get current subscriptions
@app.route('/api/subscriptions')
@login_required
def get_subscriptions():
    return jsonify(list(subscribed_symbols))


@app.route('/account')
@login_required
def account():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    credentials = FyersCredential.query.filter_by(user_id=user_id).first()

    return render_template('account.html', user=user, credentials=credentials)


@app.route('/disconnect-fyers')
@login_required
def disconnect_fyers():
    user_id = session.get('user_id')
    creds = FyersCredential.query.filter_by(user_id=user_id).first()

    if creds:
        creds.access_token = None
        creds.token_expiry = None
        db.session.commit()
        flash('Fyers account disconnected successfully', 'success')

    return redirect(url_for('connect_fyers'))


@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    user_id = session.get('user_id')
    user = User.query.get(user_id)

    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_new_password = request.form.get('confirm_new_password')

    if not all([current_password, new_password, confirm_new_password]):
        flash('All fields are required', 'danger')
        return redirect(url_for('account'))

    if not user.check_password(current_password):
        flash('Current password is incorrect', 'danger')
        return redirect(url_for('account'))

    if new_password != confirm_new_password:
        flash('New passwords do not match', 'danger')
        return redirect(url_for('account'))

    user.set_password(new_password)
    db.session.commit()

    flash('Password updated successfully', 'success')
    return redirect(url_for('account'))


# Update the get_historical_data function in fyersApp.py with better error handling

@app.route('/api/historical_data')
@login_required
def get_historical_data():
    """Get historical data for charting with comprehensive error handling"""
    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        return jsonify({'success': False, 'error': 'Fyers client not available'}), 400

    symbol = request.args.get('symbol', 'NSE:RELIANCE-EQ')
    timeframe = request.args.get('timeframe', '5')
    interval = request.args.get('interval', '5')

    try:
        # Clean and format the symbol for Fyers API
        api_symbol = format_symbol_for_fyers(symbol)

        # Calculate date range using trading days
        import pytz
        ist = pytz.timezone('Asia/Kolkata')

        # Get last trading day
        end_date = get_last_trading_day()

        # Calculate start date
        timeframe_days = int(timeframe)
        start_date = end_date - timedelta(days=timeframe_days)

        # Ensure start_date is also a trading day
        while start_date.weekday() > 4:  # Skip weekends
            start_date = start_date - timedelta(days=1)

        # Format dates for Fyers API
        from_date_str = start_date.strftime('%Y-%m-%d')
        to_date_str = end_date.strftime('%Y-%m-%d')

        # Map interval to Fyers API format
        interval_mapping = {
            '1': '1',
            '5': '5',
            '15': '15',
            '60': '60',
            'D': 'D'
        }

        fyers_interval = interval_mapping.get(interval, '5')

        # Prepare the request data
        historical_data = {
            "symbol": api_symbol,
            "resolution": fyers_interval,
            "date_format": "1",
            "range_from": from_date_str,
            "range_to": to_date_str,
            "cont_flag": "1"
        }

        print(
            f"[HISTORICAL] Requesting data for {api_symbol}: {from_date_str} to {to_date_str}, interval: {fyers_interval}")

        # Make the API call
        response = fyers_client.history(historical_data)
        print(f"[HISTORICAL] Fyers response: status={response.get('s')}, candles={len(response.get('candles', []))}")

        if response and response.get('s') == 'ok' and 'candles' in response:
            candles = response['candles']

            if not candles:
                print("[HISTORICAL] No candles in response")
                return jsonify({
                    'success': False,
                    'data': [],
                    'error': 'No candles in response',
                    'fyers_response': response
                })

            # Process the data for TradingView Lightweight Charts
            processed_data = []
            for i, candle in enumerate(candles):
                if len(candle) >= 6:
                    try:
                        timestamp = int(candle[0])
                        dt = datetime.fromtimestamp(timestamp, tz=ist)

                        processed_candle = {
                            'datetime': dt.isoformat(),
                            'timestamp': timestamp,
                            'time': timestamp,  # TradingView format
                            'open': float(candle[1]),
                            'high': float(candle[2]),
                            'low': float(candle[3]),
                            'close': float(candle[4]),
                            'volume': int(candle[5]) if len(candle) > 5 else 0
                        }

                        # Validate OHLC data
                        if (processed_candle['high'] < processed_candle['low'] or
                                processed_candle['open'] < 0 or processed_candle['close'] < 0):
                            print(f"[HISTORICAL] Invalid candle data at index {i}: {processed_candle}")
                            continue

                        processed_data.append(processed_candle)

                    except (ValueError, TypeError) as e:
                        print(f"[HISTORICAL] Error processing candle {i}: {e}")
                        continue
                else:
                    print(f"[HISTORICAL] Insufficient candle data at index {i}: {len(candle)} fields")

            print(f"[HISTORICAL] Successfully processed {len(processed_data)} candles")

            return jsonify({
                'success': True,
                'data': processed_data,
                'symbol': symbol,
                'api_symbol': api_symbol,
                'timeframe': timeframe,
                'interval': interval,
                'count': len(processed_data),
                'date_range': {
                    'from': from_date_str,
                    'to': to_date_str,
                    'from_weekday': start_date.strftime('%A'),
                    'to_weekday': end_date.strftime('%A')
                },
                'fyers_request': historical_data,
                'raw_candle_count': len(candles)
            })

        else:
            error_message = f"API Error: status={response.get('s')}, message={response.get('message', 'Unknown')}"
            print(f"[HISTORICAL] {error_message}")

            return jsonify({
                'success': False,
                'data': [],
                'symbol': symbol,
                'api_symbol': api_symbol,
                'error': error_message,
                'fyers_response': response,
                'fyers_request': historical_data
            })

    except Exception as e:
        error_msg = f"Exception in historical data: {str(e)}"
        print(f"[HISTORICAL] {error_msg}")
        import traceback
        traceback.print_exc()

        return jsonify({
            'success': False,
            'error': error_msg,
            'symbol': symbol,
            'timeframe': timeframe,
            'interval': interval
        }), 500


@app.route('/funds')
@login_required
def funds():
    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        # Check if credentials exist
        creds = FyersCredential.query.filter_by(user_id=user_id).first()
        if not creds or not creds.client_id or not creds.client_secret:
            flash('Please add your Fyers API credentials first', 'warning')
            return redirect(url_for('manage_credentials'))
        else:
            flash('Please connect your Fyers account', 'warning')
            return redirect(url_for('connect_fyers'))

    try:
        funds_data = fyers_client.funds()
        return render_template('funds.html',
                               funds=funds_data,
                               current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        flash(f'Error fetching fund data: {str(e)}', 'danger')
        return render_template('funds.html',
                               funds=None,
                               current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@app.route('/holdings')
@login_required
def holdings():
    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        # Check if credentials exist
        creds = FyersCredential.query.filter_by(user_id=user_id).first()
        if not creds or not creds.client_id or not creds.client_secret:
            flash('Please add your Fyers API credentials first', 'warning')
            return redirect(url_for('manage_credentials'))
        else:
            flash('Please connect your Fyers account', 'warning')
            return redirect(url_for('connect_fyers'))

    try:
        holdings_data = fyers_client.holdings()
        return render_template('holdings.html',
                               holdings=holdings_data,
                               current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        flash(f'Error fetching holdings data: {str(e)}', 'danger')
        return render_template('holdings.html',
                               holdings=None,
                               current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@app.route('/update-profile', methods=['POST'])
@login_required
def update_profile():
    user_id = session.get('user_id')
    user = User.query.get(user_id)

    username = request.form.get('username')
    email = request.form.get('email')

    if not username or not email:
        flash('Username and email are required', 'danger')
        return redirect(url_for('account'))

    # Check if username is taken by another user
    existing_user = User.query.filter(User.username == username, User.id != user_id).first()
    if existing_user:
        flash('Username is already taken', 'danger')
        return redirect(url_for('account'))

    # Check if email is taken by another user
    existing_email = User.query.filter(User.email == email, User.id != user_id).first()
    if existing_email:
        flash('Email is already registered', 'danger')
        return redirect(url_for('account'))

    # Update user information
    user.username = username
    user.email = email
    db.session.commit()

    # Update session
    session['username'] = username

    flash('Profile updated successfully', 'success')
    return redirect(url_for('account'))


def get_last_trading_day(date=None):
    """Get the last trading day (excludes weekends)"""
    import pytz
    from datetime import datetime, timedelta

    if date is None:
        date = datetime.now(pytz.timezone('Asia/Kolkata'))

    # If it's weekend, go back to Friday
    while date.weekday() > 4:  # Monday=0, Sunday=6
        date = date - timedelta(days=1)

    return date


def get_trading_date_range(days_back=1):
    """Get trading date range excluding weekends"""
    import pytz
    from datetime import datetime, timedelta

    ist = pytz.timezone('Asia/Kolkata')
    end_date = get_last_trading_day()

    # Go back the specified number of trading days
    start_date = end_date
    trading_days_found = 0

    while trading_days_found < days_back:
        start_date = start_date - timedelta(days=1)
        # Skip weekends
        if start_date.weekday() < 5:  # Monday=0, Friday=4
            trading_days_found += 1

    return start_date, end_date


# route for debugging
@app.route('/debug/historical/<symbol>')
@login_required
def debug_historical(symbol):
    """Debug route to test historical data fetching"""
    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        return jsonify({'error': 'No Fyers client available'})

    try:
        # Get last trading day range
        start_date, end_date = get_trading_date_range(days_back=5)  # Get 5 trading days

        # Test with multiple intervals and symbols
        test_symbols = [symbol, 'NSE:RELIANCE-EQ', 'NSE:TCS-EQ']
        test_intervals = ['1', '5', '15', '60', 'D']

        results = {}

        for test_symbol in test_symbols:
            results[test_symbol] = {}

            for interval in test_intervals:
                historical_data = {
                    "symbol": test_symbol,
                    "resolution": interval,
                    "date_format": "1",
                    "range_from": start_date.strftime('%Y-%m-%d'),
                    "range_to": end_date.strftime('%Y-%m-%d'),
                    "cont_flag": "1"
                }

                try:
                    response = fyers_client.history(historical_data)
                    results[test_symbol][interval] = {
                        'request': historical_data,
                        'response_status': response.get('s'),
                        'candle_count': len(response.get('candles', [])),
                        'sample_candle': response.get('candles', [])[:1] if response.get('candles') else None,
                        'message': response.get('message', ''),
                        'code': response.get('code', 0)
                    }
                except Exception as e:
                    results[test_symbol][interval] = {'error': str(e)}

        return jsonify({
            'trading_date_range': {
                'start': start_date.strftime('%Y-%m-%d'),
                'end': end_date.strftime('%Y-%m-%d'),
                'start_weekday': start_date.strftime('%A'),
                'end_weekday': end_date.strftime('%A')
            },
            'current_time': datetime.now().isoformat(),
            'results': results
        })

    except Exception as e:
        return jsonify({'error': str(e)})


# Add this enhanced debugging route to fyersApp.py

@app.route('/debug/chart-data/<symbol>')
@login_required
def debug_chart_data(symbol):
    """Debug route specifically for chart data testing"""
    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        return jsonify({'error': 'No Fyers client available'})

    try:
        # Get last trading day
        end_date = get_last_trading_day()
        start_date = end_date - timedelta(days=5)

        # Test the exact same call as the frontend
        historical_data = {
            "symbol": symbol,
            "resolution": "5",
            "date_format": "1",
            "range_from": start_date.strftime('%Y-%m-%d'),
            "range_to": end_date.strftime('%Y-%m-%d'),
            "cont_flag": "1"
        }

        response = fyers_client.history(historical_data)

        # Process data exactly like the main API
        processed_data = []
        if response and response.get('s') == 'ok' and 'candles' in response:
            import pytz
            ist = pytz.timezone('Asia/Kolkata')

            for candle in response['candles']:
                if len(candle) >= 6:
                    timestamp = int(candle[0])
                    dt = datetime.fromtimestamp(timestamp, tz=ist)

                    processed_data.append({
                        'datetime': dt.isoformat(),
                        'timestamp': timestamp,
                        'time': timestamp,
                        'open': float(candle[1]),
                        'high': float(candle[2]),
                        'low': float(candle[3]),
                        'close': float(candle[4]),
                        'volume': int(candle[5]) if len(candle) > 5 else 0
                    })

        return jsonify({
            'symbol': symbol,
            'request': historical_data,
            'raw_response': response,
            'processed_count': len(processed_data),
            'sample_processed': processed_data[:3] if processed_data else [],
            'sample_raw': response.get('candles', [])[:3] if response else [],
            'tradingview_format_sample': {
                'time': processed_data[0]['time'] if processed_data else None,
                'open': processed_data[0]['open'] if processed_data else None,
                'high': processed_data[0]['high'] if processed_data else None,
                'low': processed_data[0]['low'] if processed_data else None,
                'close': processed_data[0]['close'] if processed_data else None
            } if processed_data else None
        })

    except Exception as e:
        return jsonify({'error': str(e), 'traceback': str(e.__traceback__)})


@app.route('/chart-test')
@login_required
def chart_test():
    """Simple chart test page"""
    return render_template('chart_test.html')


@app.route('/realtime-market')
@login_required
def realtime_market():
    """Enhanced real-time market data view with comprehensive features"""
    user_id = session.get('user_id')
    fyers_client = get_fyers_client(user_id)

    if not fyers_client:
        flash('Please connect your Fyers account first', 'warning')
        return redirect(url_for('connect_fyers'))

    # Get symbol from query parameters or use default
    fy_token = request.args.get('fy_token')
    symbol = request.args.get('symbol', 'NSE:RELIANCE-EQ')

    # If fy_token is provided, get the instrument and symbol
    instrument = None
    if fy_token:
        instrument = Instrument.query.filter_by(fy_token=fy_token).first()
        if instrument:
            symbol = f"{instrument.exchange}:{instrument.symbol}"
            symbol = clean_symbol(symbol)

    # Initialize WebSocket if not already started
    creds = FyersCredential.query.filter_by(user_id=user_id).first()
    if creds and creds.access_token:
        if 'main' not in websocket_connections:
            start_websocket(creds.access_token)

    return render_template(
        'realtime_market.html',
        symbol=symbol,
        instrument=instrument
    )


if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)

    # Start heartbeat for WebSocket connections
    start_heartbeat()

    print("Starting FYERS Trading Dashboard with Live Market Data...")
    print("Access the application at http://127.0.0.1:5000")
    print("Live charts available at http://127.0.0.1:5000/market/live")

    socketio.run(app, port=5000, debug=True, allow_unsafe_werkzeug=True)
