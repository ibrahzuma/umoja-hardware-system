class ApiService {
    constructor(baseUrl = '/api') {
        this.baseUrl = baseUrl;
    }

    async request(endpoint, method = 'GET', data = null) {
        const headers = {
            'Content-Type': 'application/json',
            'X-CSRFToken': CSRF_TOKEN
        };

        const config = {
            method,
            headers,
        };

        if (data) {
            config.body = JSON.stringify(data);
        }

        const fullUrl = endpoint.startsWith(this.baseUrl) ? endpoint : `${this.baseUrl}${endpoint}`;
        try {
            const response = await fetch(fullUrl, config);
            if (!response.ok) {
                let errorBody;
                try {
                    errorBody = await response.json();
                } catch (e) { errorBody = {}; }

                const errorMessage = Array.isArray(errorBody.error)
                    ? errorBody.error.join('\n')
                    : (errorBody.error || errorBody.detail || response.statusText);

                const error = new Error(errorMessage);
                error.error = errorBody.error;
                error.data = errorBody;
                throw error;
            }
            if (response.status === 204) return null;
            return await response.json();
        } catch (error) {
            console.error('API Request Failed:', error);
            throw error;
        }
    }

    get(endpoint) {
        return this.request(endpoint, 'GET');
    }

    post(endpoint, data) {
        return this.request(endpoint, 'POST', data);
    }

    put(endpoint, data) {
        return this.request(endpoint, 'PUT', data);
    }

    delete(endpoint) {
        return this.request(endpoint, 'DELETE');
    }
}
