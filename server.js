// Case-Taking "new" — minimal static server (zero dependencies).
// Run: node server.js   →  http://localhost:8001
const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = 8001; // pinned — some shells set PORT=0
const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css',
  '.js': 'text/javascript',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
};

http.createServer((req, res) => {
  let url = decodeURIComponent((req.url || '/').split('?')[0]);
  if (url === '/') url = '/index.html';
  const file = path.join(process.cwd(), url);
  try {
    const body = fs.readFileSync(file);
    res.setHeader('Content-Type', TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream');
    res.end(body);
  } catch (e) {
    res.statusCode = 404;
    res.end('Not found');
  }
}).listen(PORT, () => console.log('Case-Taking "new" running — open http://localhost:' + PORT));