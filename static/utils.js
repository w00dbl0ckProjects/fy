// static/utils.js

// Utility functions for the Fyers Trading Dashboard

class DashboardUtils {
    static formatCurrency(amount, currency = '₹') {
        if (amount === null || amount === undefined) return '-';
        const num = parseFloat(amount);
        if (isNaN(num)) return '-';

        return `${currency}${num.toLocaleString('en-IN', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        })}`;
    }

    static formatNumber(num, decimals = 2) {
        if (num === null || num === undefined) return '-';
        const number = parseFloat(num);
        if (isNaN(number)) return '-';

        return number.toLocaleString('en-IN', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        });
    }

    static formatVolume(volume) {
        if (volume === null || volume === undefined) return '-';
        const vol = parseInt(volume);
        if (isNaN(vol)) return '-';

        if (vol >= 10000000) { // 1 Crore
            return (vol / 10000000).toFixed(1) + 'Cr';
        } else if (vol >= 100000) { // 1 Lakh
            return (vol / 100000).toFixed(1) + 'L';
        } else if (vol >= 1000) {
            return (vol / 1000).toFixed(1) + 'K';
        }
        return vol.toString();
    }

    static formatTime(timestamp) {
        if (!timestamp) return '-';

        const date = new Date(timestamp);
        if (isNaN(date.getTime())) return '-';

        return date.toLocaleTimeString('en-IN', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }

    static formatDate(date) {
        if (!date) return '-';

        const d = new Date(date);
        if (isNaN(d.getTime())) return '-';

        return d.toLocaleDateString('en-IN', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    }

    static getChangeClass(value, prefix = 'text') {
        if (value > 0) return `${prefix}-green-600`;
        if (value < 0) return `${prefix}-red-600`;
        return `${prefix}-gray-900`;
    }

    static formatChange(value, showSign = true) {
        if (value === null || value === undefined) return '-';
        const num = parseFloat(value);
        if (isNaN(num)) return '-';

        const sign = showSign && num > 0 ? '+' : '';
        return `${sign}${num.toFixed(2)}`;
    }

    static formatPercentage(value, showSign = true) {
        if (value === null || value === undefined) return '-';
        const num = parseFloat(value);
        if (isNaN(num)) return '-';

        const sign = showSign && num > 0 ? '+' : '';
        return `${sign}${num.toFixed(2)}%`;
    }

    static debounce(func, wait, immediate = false) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                timeout = null;
                if (!immediate) func(...args);
            };
            const callNow = immediate && !timeout;
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
            if (callNow) func(...args);
        };
    }

    static throttle(func, limit) {
        let inThrottle;
        return function(...args) {
            if (!inThrottle) {
                func.apply(this, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    }

    static showNotification(message, type = 'info', duration = 5000) {
        const notification = document.createElement('div');
        notification.className = `fixed top-4 right-4 z-50 max-w-sm w-full bg-white shadow-lg rounded-lg pointer-events-auto notification-enter`;

        const bgColor = {
            'success': 'bg-green-50 border-green-200',
            'error': 'bg-red-50 border-red-200',
            'warning': 'bg-yellow-50 border-yellow-200',
            'info': 'bg-blue-50 border-blue-200'
        }[type] || 'bg-gray-50 border-gray-200';

        const iconColor = {
            'success': 'text-green-400',
            'error': 'text-red-400',
            'warning': 'text-yellow-400',
            'info': 'text-blue-400'
        }[type] || 'text-gray-400';

        const icons = {
            'success': `<path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path>`,
            'error': `<path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path>`,
            'warning': `<path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"></path>`,
            'info': `<path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"></path>`
        };

        notification.innerHTML = `
            <div class="border ${bgColor} rounded-lg p-4">
                <div class="flex">
                    <div class="flex-shrink-0">
                        <svg class="h-5 w-5 ${iconColor}" fill="currentColor" viewBox="0 0 20 20">
                            ${icons[type] || icons.info}
                        </svg>
                    </div>
                    <div class="ml-3 w-0 flex-1">
                        <p class="text-sm font-medium text-gray-900">${message}</p>
                    </div>
                    <div class="ml-4 flex-shrink-0 flex">
                        <button class="bg-white rounded-md inline-flex text-gray-400 hover:text-gray-500 focus:outline-none" onclick="this.parentElement.parentElement.parentElement.parentElement.remove()">
                            <span class="sr-only">Close</span>
                            <svg class="h-5 w-5" fill="currentColor" viewBox="0 0 20 20">
                                <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(notification);

        // Trigger enter animation
        setTimeout(() => {
            notification.classList.remove('notification-enter');
            notification.classList.add('notification-enter-active');
        }, 10);

        // Auto remove after duration
        if (duration > 0) {
            setTimeout(() => {
                notification.classList.remove('notification-enter-active');
                notification.classList.add('notification-exit-active');
                setTimeout(() => notification.remove(), 300);
            }, duration);
        }
    }

    static createLoadingSpinner(size = 'medium') {
        const sizes = {
            'small': 'h-4 w-4',
            'medium': 'h-8 w-8',
            'large': 'h-12 w-12'
        };

        const spinner = document.createElement('div');
        spinner.className = `animate-spin rounded-full border-2 border-blue-600 border-t-transparent ${sizes[size]}`;
        return spinner;
    }

    static isMarketOpen() {
        const now = new Date();
        const day = now.getDay();
        const hours = now.getHours();
        const minutes = now.getMinutes();
        const time = hours * 100 + minutes;

        // Weekend check (Saturday = 6, Sunday = 0)
        if (day === 0 || day === 6) return false;

        // Market hours: 9:15 AM to 3:30 PM IST
        return time >= 915 && time <= 1530;
    }

    static getMarketStatus() {
        if (this.isMarketOpen()) {
            return { status: 'open', message: 'Market is open' };
        }

        const now = new Date();
        const day = now.getDay();
        const hours = now.getHours();
        const minutes = now.getMinutes();
        const time = hours * 100 + minutes;

        if (day === 0 || day === 6) {
            return { status: 'closed', message: 'Market closed - Weekend' };
        }

        if (time < 915) {
            return { status: 'pre-open', message: 'Market opens at 9:15 AM' };
        }

        return { status: 'closed', message: 'Market closed for the day' };
    }

    static validateFyersToken(token) {
        if (!token) return false;

        // Basic validation for Fyers token format
        // Format: EXCHANGE:SYMBOL-SERIES or similar
        const pattern = /^[A-Z]+:[A-Z0-9]+-?[A-Z0-9]*$/i;
        return pattern.test(token);
    }

    static copyToClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text);
        } else {
            // Fallback for older browsers
            const textArea = document.createElement('textarea');
            textArea.value = text;
            textArea.style.position = 'fixed';
            textArea.style.left = '-999999px';
            textArea.style.top = '-999999px';
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();

            return new Promise((resolve, reject) => {
                document.execCommand('copy') ? resolve() : reject();
                textArea.remove();
            });
        }
    }
}

// Export for use in other modules
window.DashboardUtils = DashboardUtils;