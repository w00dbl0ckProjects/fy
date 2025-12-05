// static/watchlist.js

class WatchlistManager {
    constructor() {
        this.socket = null;
        this.subscribedSymbols = new Set();
        this.lastPrices = {};
        this.updateQueue = [];
        this.isProcessing = false;

        this.initializeSocket();
        this.setupEventListeners();
    }

    initializeSocket() {
        this.socket = io();

        this.socket.on('connect', () => {
            console.log('Watchlist WebSocket connected');
            this.updateConnectionStatus('connected');
        });

        this.socket.on('disconnect', () => {
            console.log('Watchlist WebSocket disconnected');
            this.updateConnectionStatus('disconnected');
        });

        this.socket.on('market_data', (data) => {
            this.queueUpdate(data);
        });

        this.socket.on('subscribed', (data) => {
            console.log('Subscribed to:', data.symbol);
            this.markSymbolAsSubscribed(data.symbol);
        });
    }

    queueUpdate(data) {
        this.updateQueue.push(data);
        if (!this.isProcessing) {
            this.processUpdateQueue();
        }
    }

    async processUpdateQueue() {
        this.isProcessing = true;

        while (this.updateQueue.length > 0) {
            const data = this.updateQueue.shift();
            await this.updateMarketData(data);

            // Small delay to prevent UI blocking
            if (this.updateQueue.length > 10) {
                await new Promise(resolve => setTimeout(resolve, 1));
            }
        }

        this.isProcessing = false;
    }

    async updateMarketData(data) {
        const symbol = data.symbol;
        if (!symbol) return;

        const symbolId = this.cleanSymbolId(symbol);
        const row = document.querySelector(`[data-symbol="${symbolId}"]`);

        if (!row) return;

        // Update price with animation
        await this.updatePrice(symbolId, data.ltp);

        // Update other fields
        this.updateChange(symbolId, data.ch, data.chp);
        this.updateVolume(symbolId, data.volume);

        // Update timestamp
        this.updateTimestamp();
    }

    async updatePrice(symbolId, newPrice) {
        if (newPrice === undefined) return;

        const priceEl = document.getElementById(`ltp-${symbolId}`);
        if (!priceEl) return;

        const price = parseFloat(newPrice);
        const lastPrice = this.lastPrices[symbolId];

        // Update display
        priceEl.textContent = `₹${price.toFixed(2)}`;

        // Add animation based on price movement
        if (lastPrice !== undefined) {
            if (price > lastPrice) {
                priceEl.classList.add('price-up');
                priceEl.className = priceEl.className.replace(/text-\w+-\d+/, 'text-green-600');
            } else if (price < lastPrice) {
                priceEl.classList.add('price-down');
                priceEl.className = priceEl.className.replace(/text-\w+-\d+/, 'text-red-600');
            }

            // Remove animation class after animation completes
            setTimeout(() => {
                priceEl.classList.remove('price-up', 'price-down');
            }, 500);
        }

        this.lastPrices[symbolId] = price;
    }

    updateChange(symbolId, change, changePct) {
        if (change !== undefined) {
            const changeEl = document.getElementById(`change-${symbolId}`);
            if (changeEl) {
                const changeVal = parseFloat(change);
                changeEl.textContent = `${changeVal >= 0 ? '+' : ''}${changeVal.toFixed(2)}`;
                changeEl.className = changeVal >= 0 ?
                    'text-sm font-semibold text-green-600' :
                    'text-sm font-semibold text-red-600';
            }
        }

        if (changePct !== undefined) {
            const changePctEl = document.getElementById(`change-pct-${symbolId}`);
            if (changePctEl) {
                const changePctVal = parseFloat(changePct);
                changePctEl.textContent = `${changePctVal >= 0 ? '+' : ''}${changePctVal.toFixed(2)}%`;
                changePctEl.className = changePctVal >= 0 ?
                    'text-sm font-semibold text-green-600' :
                    'text-sm font-semibold text-red-600';
            }
        }
    }

    updateVolume(symbolId, volume) {
        if (volume === undefined) return;

        const volumeEl = document.getElementById(`volume-${symbolId}`);
        if (volumeEl) {
            volumeEl.textContent = this.formatVolume(volume);
        }
    }

    formatVolume(volume) {
        const vol = parseInt(volume);
        if (vol >= 1000000) {
            return (vol / 1000000).toFixed(1) + 'M';
        } else if (vol >= 1000) {
            return (vol / 1000).toFixed(1) + 'K';
        }
        return vol.toString();
    }

    subscribeToSymbol(symbol) {
        if (!this.subscribedSymbols.has(symbol)) {
            this.socket.emit('subscribe', { symbol: symbol });
            this.subscribedSymbols.add(symbol);
        }
    }

    unsubscribeFromSymbol(symbol) {
        if (this.subscribedSymbols.has(symbol)) {
            this.socket.emit('unsubscribe', { symbol: symbol });
            this.subscribedSymbols.delete(symbol);
        }
    }

    updateConnectionStatus(status) {
        const indicator = document.getElementById('status-indicator');
        const statusText = document.getElementById('status-text');

        if (!indicator || !statusText) return;

        switch (status) {
            case 'connected':
                indicator.className = 'w-2 h-2 bg-green-500 rounded-full mr-2 animate-pulse';
                statusText.textContent = 'Live - Connected';
                break;
            case 'disconnected':
                indicator.className = 'w-2 h-2 bg-red-500 rounded-full mr-2';
                statusText.textContent = 'Disconnected';
                break;
            default:
                indicator.className = 'w-2 h-2 bg-gray-500 rounded-full mr-2';
                statusText.textContent = 'Connecting...';
        }
    }

    markSymbolAsSubscribed(symbol) {
        const symbolId = this.cleanSymbolId(symbol);
        const statusEl = document.getElementById(`status-${symbolId}`);
        if (statusEl) {
            statusEl.className = 'flex-shrink-0 h-3 w-3 rounded-full mr-3 bg-green-400 animate-pulse';
        }
    }

    cleanSymbolId(symbol) {
        return symbol.replace(/[^a-zA-Z0-9]/g, '-');
    }

    updateTimestamp() {
        const timestampEl = document.getElementById('update-time');
        if (timestampEl) {
            timestampEl.textContent = new Date().toLocaleTimeString();
        }
    }

    setupEventListeners() {
        // Handle page visibility changes
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                console.log('Page hidden, pausing updates');
            } else {
                console.log('Page visible, resuming updates');
            }
        });

        // Handle window beforeunload
        window.addEventListener('beforeunload', () => {
            this.cleanup();
        });
    }

    cleanup() {
        if (this.socket) {
            this.subscribedSymbols.forEach(symbol => {
                this.socket.emit('unsubscribe', { symbol: symbol });
            });
            this.socket.disconnect();
        }
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname.includes('watchlist-live')) {
        window.watchlistManager = new WatchlistManager();
    }
});