import puppeteer from 'puppeteer'

const browser = await puppeteer.launch({ headless: true })
const page = await browser.newPage()

await page.setViewport({ width: 3840, height: 2160, deviceScaleFactor: 1 })
await page.goto('http://localhost:5173/', { waitUntil: 'networkidle0', timeout: 30000 })
await new Promise(r => setTimeout(r, 2000))

await page.screenshot({
  path: 'screenshot_main_4k.png',
  fullPage: true,
})

await browser.close()
console.log('Done: screenshot_main_4k.png')
