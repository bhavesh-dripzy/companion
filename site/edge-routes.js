function handler(event) {
  var request = event.request;
  var host = request.headers.host ? request.headers.host.value : '';
  var uri = request.uri;
  var parts = [];
  Object.keys(request.querystring || {}).forEach(function (key) {
    var item = request.querystring[key];
    var values = item.multiValue || [item];
    values.forEach(function (value) {
      // CloudFront supplies URL-encoded query components. Preserve them.
      parts.push(key + '=' + value.value);
    });
  });
  var query = parts.length ? '?' + parts.join('&') : '';
  if (host === 'www.jatayulabs.com') {
    return {
      statusCode: 301,
      statusDescription: 'Moved Permanently',
      headers: {
        location: { value: 'https://jatayulabs.com' + uri + query },
        'cache-control': { value: 'public,max-age=300' }
      }
    };
  }
  if (uri === '/product') {
    return {
      statusCode: 301,
      statusDescription: 'Moved Permanently',
      headers: { location: { value: '/product/' + query } }
    };
  }
  if (uri === '/privacy') request.uri = '/privacy.html';
  else if (uri.charAt(uri.length - 1) === '/') request.uri = uri + 'index.html';
  return request;
}
