export function basicAuth(req, res, next) {
  const user = process.env.DASHBOARD_USER;
  const pass = process.env.DASHBOARD_PASS;

  if (process.env.NODE_ENV !== 'production' && (!user || !pass)) {
    return next();
  }

  const authHeader = req.headers['authorization'] || '';
  const [scheme, encoded] = authHeader.split(' ');

  if (scheme !== 'Basic' || !encoded) {
    res.set('WWW-Authenticate', 'Basic realm="Tripwire Dashboard"');
    return res.status(401).send('Authentication required.');
  }

  const decoded = Buffer.from(encoded, 'base64').toString('utf8');
  const colonIdx = decoded.indexOf(':');
  const incomingUser = decoded.slice(0, colonIdx);
  const incomingPass = decoded.slice(colonIdx + 1);

  if (incomingUser === user && incomingPass === pass) {
    return next();
  }

  res.set('WWW-Authenticate', 'Basic realm="Tripwire Dashboard"');
  return res.status(401).send('Invalid credentials.');
}
