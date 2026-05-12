const express = require('express');
const path = require('path');
const { createProxyMiddleware } = require('http-proxy-middleware');

const app = express();

// Proxy requests to the Python backend
app.use('/api', createProxyMiddleware({ 
    target: 'http://127.0.0.1:5000', 
    changeOrigin: true,
    pathRewrite: {
        '^/api': '', // remove /api from the start of the url
    },
}));

// Serve the React app
app.use(express.static(path.join(__dirname, '..', 'frontend', 'dist')));

const port = process.env.PORT || 3000;
app.listen(port, '127.0.0.1', () => {
    console.log(`Server listening on http://127.0.0.1:${port}`);
});
