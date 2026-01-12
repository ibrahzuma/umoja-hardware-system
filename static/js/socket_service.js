class SocketService {
    constructor(endpoint) {
        // Construct WebSocket URL
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.url = `${protocol}//${window.location.host}/ws/${endpoint}/`;
        this.socket = null;
        this.callbacks = {};
    }

    connect() {
        this.socket = new WebSocket(this.url);

        this.socket.onopen = () => {
            console.log('WebSocket Connected');
        };

        this.socket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.trigger(data.type, data);
        };

        this.socket.onclose = () => {
            console.log('WebSocket Disconnected. Reconnecting in 5s...');
            setTimeout(() => this.connect(), 5000);
        };

        this.socket.onerror = (error) => {
            console.error('WebSocket Error:', error);
        };
    }

    on(eventType, callback) {
        if (!this.callbacks[eventType]) {
            this.callbacks[eventType] = [];
        }
        this.callbacks[eventType].push(callback);
    }

    trigger(eventType, data) {
        if (this.callbacks[eventType]) {
            this.callbacks[eventType].forEach(callback => callback(data));
        }
    }
}
