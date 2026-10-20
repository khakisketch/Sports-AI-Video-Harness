// Run with Node 22+ after scripts/hyperframes.py preview --background.
// Only localhost Studio playback is exercised; no media-generation controls.
import { createRequire } from "node:module";
import { realpathSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const project = path.join(root, "episodes/EP001/previews/hyperframes-v1");
const req = createRequire(realpathSync(path.join(root, "tools/hyperframes/node_modules/hyperframes/package.json")));
const { default: puppeteer } = await import(req.resolve("puppeteer-core"));
const browser = await puppeteer.launch({ executablePath: process.env.SPORTS_PREVIEW_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const results = { url: "http://127.0.0.1:3017/#project/hyperframes-v1", samples: [], pageErrors: [], blockedExternalRequests: [], remoteGenerationCalls: 0 };

try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1500, height: 1000 });
  await page.setRequestInterception(true);
  page.on("request", request => {
    const url = request.url();
    if (/^(data:|blob:|about:)/.test(url) || ["127.0.0.1", "localhost", "[::1]"].includes(new URL(url).hostname)) return request.continue();
    results.blockedExternalRequests.push(url);
    return request.abort();
  });
  page.on("pageerror", error => results.pageErrors.push(error.message));
  await page.goto(results.url, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some(button => button.innerText.includes("/00:30")), { timeout: 15000 });
  // Studio replaces its bootstrap iframe during hydration. Select the settled
  // frame, rather than retaining the first temporary iframe with the same URL.
  await new Promise(resolve => setTimeout(resolve, 3500));
  const frame = page.frames().find(candidate => candidate.url().includes("/api/projects/hyperframes-v1/preview"));
  if (!frame) throw new Error("Studio composition iframe is missing.");
  async function sample() {
    return frame.evaluate(() => {
      const video = document.querySelector("#bridge-video");
      const active = [...document.querySelectorAll(".phase-copy")].filter(el => el.checkVisibility({ opacityProperty: true, visibilityProperty: true })).map(el => el.innerText);
      return { seconds: video.currentTime, paused: video.paused, width: video.videoWidth, height: video.videoHeight, referencesVisible: ["harin-front", "harin-ready"].every(id => document.getElementById(id).checkVisibility({ opacityProperty: true, visibilityProperty: true })), active };
    });
  }
  results.initial = await sample();
  await page.click('button[aria-label="Play"]');
  await frame.waitForFunction(() => document.querySelector("#bridge-video").currentTime > 0.2, { timeout: 5000 });
  // Observe the complete source cycle in ordinary real-time Studio playback.
  for (let i = 0; i < 16; i++) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    results.samples.push(await sample());
  }
  const times = results.samples.map(sample => sample.seconds);
  if (Math.max(...times) < 29 || times.slice(0, 12).some((time, i) => i && time < times[i - 1] - 0.1)) throw new Error("Studio did not play the complete cycle continuously.");
  await page.screenshot({ path: path.join(project, "studio-playback.png") });
  // Seek through the runtime API owned by HyperFrames, never video.currentTime.
  results.runtimeSeek = await frame.evaluate(async () => {
    const proof = [];
    for (const seconds of [16, 4, 22, 16]) {
      window.__player.pause();
      await window.__player.seek(seconds);
      if (window.__hfWaitForSeekCompletion) await window.__hfWaitForSeekCompletion();
      proof.push({ requested: seconds, actual: document.querySelector("#bridge-video").currentTime, phase: [...document.querySelectorAll(".phase-copy")].filter(el => el.checkVisibility({ opacityProperty: true, visibilityProperty: true })).map(el => el.innerText) });
    }
    return proof;
  });
  if (results.runtimeSeek.some(row => Math.abs(row.requested - row.actual) > 0.15)) throw new Error("Runtime seek did not match source time.");
  if (results.pageErrors.length) throw new Error("Studio reported JavaScript errors.");
  results.ok = true;
} catch (error) {
  results.ok = false;
  results.error = error.message;
  process.exitCode = 1;
} finally {
  writeFileSync(path.join(project, "browser-qa.json"), JSON.stringify(results, null, 2) + "\n");
  console.log(JSON.stringify(results, null, 2));
  await browser.close();
}
