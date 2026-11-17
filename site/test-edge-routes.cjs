const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, 'edge-routes.js'), 'utf8'), sandbox);
function request(uri, host = 'jatayulabs.com', querystring = {}) {
  return sandbox.handler({ request: { uri, headers: { host: { value: host } }, querystring } });
}
assert.equal(request('/').uri, '/index.html');
assert.equal(request('/product/').uri, '/product/index.html');
assert.equal(request('/product').headers.location.value, '/product/');
assert.equal(request('/product', 'jatayulabs.com', { view: { value: 'risks' } }).headers.location.value, '/product/?view=risks');
assert.equal(request('/privacy').uri, '/privacy.html');
assert.equal(request('/assets/site-abcd.js').uri, '/assets/site-abcd.js');
assert.equal(request('/.well-known/security.txt').uri, '/.well-known/security.txt');
const redirect = request('/product/', 'www.jatayulabs.com', { view: { value: 'risks' } });
assert.equal(redirect.statusCode, 301);
assert.equal(redirect.headers.location.value, 'https://jatayulabs.com/product/?view=risks');
assert.equal(request('/', 'attacker.example').uri, '/index.html');
assert.equal(request('/', 'www.jatayulabs.com', { source: { value: 'early%20access' } }).headers.location.value, 'https://jatayulabs.com/?source=early%20access');
console.log('Edge routing and canonical redirect tests passed.');
