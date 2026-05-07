import { NextResponse } from 'next/server'

// Sanctioned countries and regions per US OFAC regulations
// Cuba, Iran, North Korea, Syria
// Note: Crimea, Luhansk, Donetsk are regions within Ukraine/Russia
// blocked at the country level via RU and UA with region detection
const BLOCKED_COUNTRIES = ['CU', 'IR', 'KP', 'SY']

export function middleware(request) {
  const country = request.geo?.country
  const region  = request.geo?.region

  // Block sanctioned countries
  if(country && BLOCKED_COUNTRIES.includes(country)){
    return new NextResponse(
      `<!DOCTYPE html>
      <html><head><title>Service Unavailable</title>
      <style>body{font-family:sans-serif;background:#080a0c;color:#5a7090;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center;}h1{color:#ccd8e8;}a{color:#1db87a;}</style>
      </head><body>
      <div>
        <h1>Service Not Available</h1>
        <p>SOSLogs is not available in your region due to legal restrictions.</p>
        <p>For questions contact <a href="mailto:admin@soslogs.org">admin@soslogs.org</a></p>
      </div>
      </body></html>`,
      {
        status: 451, // 451 = Unavailable For Legal Reasons
        headers: { 'Content-Type': 'text/html' }
      }
    )
  }

  // Block Crimea (UA-43), Luhansk (UA-09), Donetsk (UA-14)
  if(country === 'UA' && region && ['43','09','14'].includes(region)){
    return new NextResponse(
      `<!DOCTYPE html>
      <html><head><title>Service Unavailable</title>
      <style>body{font-family:sans-serif;background:#080a0c;color:#5a7090;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center;}h1{color:#ccd8e8;}a{color:#1db87a;}</style>
      </head><body>
      <div>
        <h1>Service Not Available</h1>
        <p>SOSLogs is not available in your region due to legal restrictions.</p>
        <p>For questions contact <a href="mailto:admin@soslogs.org">admin@soslogs.org</a></p>
      </div>
      </body></html>`,
      {
        status: 451,
        headers: { 'Content-Type': 'text/html' }
      }
    )
  }

  return NextResponse.next()
}

export const config = {
  // Apply to all routes except static files and api
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)']
}
