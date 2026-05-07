export default function middleware(request) {
  const country = request.headers.get('x-vercel-ip-country') || ''
  const region  = request.headers.get('x-vercel-ip-country-region') || ''

  const BLOCKED_COUNTRIES = ['CU','IR','KP','SY']
  const BLOCKED_UA_REGIONS = ['43','09','14'] // Crimea, Luhansk, Donetsk

  const blocked =
    BLOCKED_COUNTRIES.includes(country) ||
    (country === 'UA' && BLOCKED_UA_REGIONS.includes(region)) ||
    (country === 'RU' && BLOCKED_UA_REGIONS.includes(region))

  if(blocked){
    return new Response(
      `<!DOCTYPE html>
<html><head><title>Service Unavailable</title>
<style>body{font-family:sans-serif;background:#080a0c;color:#5a7090;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center;}h1{color:#ccd8e8;margin-bottom:12px;}a{color:#1db87a;}</style>
</head><body><div>
<h1>Service Not Available</h1>
<p>SOSLogs is not available in your region due to legal restrictions.</p>
<p>Questions? <a href="mailto:admin@soslogs.org">admin@soslogs.org</a></p>
</div></body></html>`,
      { status: 451, headers: { 'Content-Type': 'text/html' } }
    )
  }

  // Allow request to pass through unchanged
  return fetch(request)
}

export const config = {
  matcher: '/(.*)'
}
