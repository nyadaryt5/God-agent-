"""Anti-bot-detection hardening for the browser tools.

A stock Playwright launch is trivially fingerprinted: `navigator.webdriver` is
true, `window.chrome` is missing, the UA contains "HeadlessChrome", WebGL
reports a software renderer, and CDP leaves `cdc_*` markers on the document.
Roughly a dozen signals, and any one of them is enough.

This module closes those signals, and — the part most naive stealth patches get
wrong — keeps them **mutually consistent**. A UA claiming Chrome 131 on
"Win32" alongside `navigator.userAgentData.platform === "Linux"` and a Mesa
WebGL renderer is *more* suspicious than no patch at all, because no real
browser ever produces that combination. Every value below is derived from one
coherent device profile.

Scope, stated plainly
---------------------
This defeats *passive fingerprinting*: it stops the browser advertising that
it is automated. It does not defeat behavioural analysis (mouse-signature,
request-cadence, and TLS-fingerprint models), and it does **not** attempt to
solve CAPTCHAs or rotate through proxy/identity pools — those exist to stop
abuse, and circumventing them is a different thing from not looking like a
default headless browser. Use this to test your own defences, drive your own
accounts, and automate against services you are permitted to automate.
"""
from __future__ import annotations

import json
import random
import time
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Device profiles. Each one is internally consistent: UA, platform, Client
# Hints, WebGL vendor, screen size, and CPU count all describe the same
# machine. Mixing fields across profiles is what gets naive patches caught.
# ---------------------------------------------------------------------------
PROFILES: dict[str, dict[str, Any]] = {
    "windows-chrome": {
        "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        "platform": "Win32",
        "vendor": "Google Inc.",
        "ua_data_platform": "Windows",
        "platform_version": "15.0.0",
        "full_version": "131.0.6778.86",
        "brands": [{"brand": "Google Chrome", "version": "131"},
                   {"brand": "Chromium", "version": "131"},
                   {"brand": "Not_A Brand", "version": "24"}],
        "webgl_vendor": "Google Inc. (Intel)",
        "webgl_renderer": ("ANGLE (Intel, Intel(R) UHD Graphics 620 (0x00003EA0) "
                           "Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        "plugins": ["PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer",
                    "Microsoft Edge PDF Viewer", "WebKit built-in PDF"],
        "screen": {"width": 1920, "height": 1080},
        "hardware_concurrency": 8,
        "device_memory": 8,
        "chrome_height": 85,
    },
    "macos-chrome": {
        "user_agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        "platform": "MacIntel",
        "vendor": "Google Inc.",
        "ua_data_platform": "macOS",
        "platform_version": "14.7.1",
        "full_version": "131.0.6778.86",
        "brands": [{"brand": "Google Chrome", "version": "131"},
                   {"brand": "Chromium", "version": "131"},
                   {"brand": "Not_A Brand", "version": "24"}],
        "webgl_vendor": "Google Inc. (Apple)",
        "webgl_renderer": "ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Pro, Unspecified Version)",
        "plugins": ["PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer",
                    "WebKit built-in PDF"],
        "screen": {"width": 1728, "height": 1117},
        "hardware_concurrency": 10,
        "device_memory": 8,
        "chrome_height": 85,
    },
    "linux-chrome": {
        "user_agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        "platform": "Linux x86_64",
        "vendor": "Google Inc.",
        "ua_data_platform": "Linux",
        "platform_version": "6.8.0",
        "full_version": "131.0.6778.86",
        "brands": [{"brand": "Google Chrome", "version": "131"},
                   {"brand": "Chromium", "version": "131"},
                   {"brand": "Not_A Brand", "version": "24"}],
        "webgl_vendor": "Google Inc. (Mesa)",
        "webgl_renderer": "ANGLE (Mesa, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)",
        "plugins": ["PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer",
                    "WebKit built-in PDF"],
        "screen": {"width": 1920, "height": 1080},
        "hardware_concurrency": 8,
        "device_memory": 8,
        "chrome_height": 85,
    },
}

DEFAULT_PROFILE = "windows-chrome"


def profile_names() -> list[str]:
    return sorted(PROFILES)


def get_profile(name: str, overrides: Optional[dict] = None) -> dict[str, Any]:
    """Return a profile with operator overrides applied (deep for `screen`)."""
    base = PROFILES.get(name)
    if base is None:
        base = PROFILES[DEFAULT_PROFILE]
    prof = json.loads(json.dumps(base))  # cheap deep copy
    for key, val in (overrides or {}).items():
        if key == "screen" and isinstance(val, dict):
            prof["screen"].update(val)
        elif val not in (None, "", []):
            prof[key] = val
    return prof


# ---------------------------------------------------------------------------
# Launch hardening
# ---------------------------------------------------------------------------
# Playwright passes --enable-automation by default; it is the flag that puts
# the "Chrome is being controlled by automated test software" infobar up and
# flips the AutomationControlled blink feature.
IGNORE_DEFAULT_ARGS = ["--enable-automation"]

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-ipc-flooding-protection",
    "--disable-dev-shm-usage",
    "--no-sandbox",
]


def launch_kwargs(cfg: dict, proxy: Optional[dict] = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "headless": bool(cfg.get("headless", True)),
        "args": list(LAUNCH_ARGS),
        "ignore_default_args": list(IGNORE_DEFAULT_ARGS),
    }
    if proxy and proxy.get("server"):
        # A single static proxy — corporate egress, geo-testing, or your own
        # exit IP. Deliberately not a rotation pool.
        pw: dict[str, Any] = {"server": proxy["server"]}
        if proxy.get("username"):
            pw["username"] = proxy["username"]
        if proxy.get("password"):
            pw["password"] = proxy["password"]
        if proxy.get("bypass"):
            pw["bypass"] = proxy["bypass"]
        kwargs["proxy"] = pw
    return kwargs


def context_kwargs(cfg: dict, prof: dict) -> dict[str, Any]:
    """Context-level options: locale, timezone, UA, viewport."""
    opts: dict[str, Any] = {
        "viewport": cfg.get("viewport") or {"width": 1280, "height": 900},
        "ignore_https_errors": False,
        # Setting the UA at context level (not via init script) is what makes
        # it consistent for the network layer, Sec-CH-UA headers, and JS.
        "user_agent": prof["user_agent"],
        "locale": prof.get("locale", "en-US"),
        "timezone_id": prof.get("timezone", "UTC"),
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
    }
    return opts


# ---------------------------------------------------------------------------
# Init script: the JS-level patches, injected before any page script runs.
# ---------------------------------------------------------------------------
_INIT_SCRIPT = r"""
(() => {
  const P = __PROFILE__;
  const V = __VIEWPORT__;

  // Define a property without tripping on non-configurable ones.
  const hide = (obj, prop, value, enumerable = true) => {
    try {
      Object.defineProperty(obj, prop, { get: () => value, configurable: true, enumerable });
    } catch (e) { /* non-configurable: leave it alone rather than throw */ }
  };

  // Make a patched function still report "[native code]" — a very common
  // integrity check that naive wrappers fail.
  const keepNative = (fn, name) => {
    try {
      Object.defineProperty(fn, 'toString', {
        value: () => 'function ' + name + '() { [native code] }',
        configurable: true,
      });
    } catch (e) {}
  };

  // --- 1. navigator.webdriver: the loudest single signal -------------------
  // Delete from the prototype, not the instance, so that a plain
  // `'webdriver' in navigator` check also comes back false.
  try { delete Object.getPrototypeOf(navigator).webdriver; } catch (e) {}
  if (navigator.webdriver !== undefined) { hide(navigator, 'webdriver', undefined, false); }

  // --- 2. CDP leaves cdc_ / $cdc_ markers on window and document ----------
  const stripCdc = (target) => {
    if (!target) return;
    let names;
    try { names = Object.getOwnPropertyNames(target); } catch (e) { return; }
    for (const k of names) {
      if (/^(cdc_|\$cdc_|__\$webdriverAsyncExecutor|__webdriver_script_fn|__driver_evaluate)/.test(k)) {
        try { delete target[k]; }
        catch (e) {
          try { Object.defineProperty(target, k, { value: undefined, configurable: true }); } catch (e2) {}
        }
      }
    }
  };
  stripCdc(window);
  stripCdc(document);
  try { stripCdc(Object.getPrototypeOf(document)); } catch (e) {}

  // --- 3. window.chrome: absent under automation ---------------------------
  if (!window.chrome) {
    window.chrome = {
      app: { isInstalled: false, InstallState: {}, RunningState: {},
             getDetails: () => null, getIsInstalled: () => false },
      runtime: { id: undefined, connect: () => {}, sendMessage: () => {},
                 onMessage: { addListener: () => {} },
                 onConnect: { addListener: () => {} } },
      csi: () => {}, loadTimes: () => {},
    };
  }

  // --- 4. plugins / mimeTypes ---------------------------------------------
  const pluginList = P.plugins.map((name) => ({
    name, filename: 'internal-pdf-viewer', description: name, length: 1,
  }));
  pluginList.item = (i) => pluginList[i] || null;
  pluginList.namedItem = (n) => pluginList.find((p) => p.name === n) || null;
  pluginList.refresh = () => {};
  hide(navigator, 'plugins', pluginList);

  const mimeList = [{
    type: 'application/pdf', suffixes: 'pdf',
    description: 'Portable Document Format', enabledPlugin: pluginList[0] || null,
  }];
  mimeList.item = (i) => mimeList[i] || null;
  mimeList.namedItem = (n) => mimeList.find((m) => m.type === n) || null;
  hide(navigator, 'mimeTypes', mimeList);

  // --- 5. navigator fields -------------------------------------------------
  hide(navigator, 'languages', P.languages);
  hide(navigator, 'language', P.languages[0]);
  hide(navigator, 'platform', P.platform);
  hide(navigator, 'vendor', P.vendor);
  hide(navigator, 'hardwareConcurrency', P.hardware_concurrency);
  hide(navigator, 'deviceMemory', P.device_memory);
  hide(navigator, 'maxTouchPoints', 0);
  hide(navigator, 'pdfViewerEnabled', true);
  hide(navigator, 'doNotTrack', null);
  hide(navigator, 'connection', { effectiveType: '4g', rtt: 50, downlink: 10,
                                  saveData: false, onchange: null });

  // --- 6. Client Hints (navigator.userAgentData) ---------------------------
  // Must agree with the UA string or the mismatch is itself a signal.
  const uaData = {
    brands: P.brands,
    mobile: false,
    platform: P.ua_data_platform,
    architecture: 'x86',
    bitness: '64',
    getHighEntropyValues: async () => ({
      architecture: 'x86', bitness: '64', model: '',
      platformVersion: P.platform_version,
      uaFullVersion: P.full_version,
      fullVersionList: P.brands.map((b) => ({ brand: b.brand, version: b.version + '.0.0.0' })),
      wow64: false,
    }),
    toJSON: () => ({ brands: P.brands, mobile: false, platform: P.ua_data_platform,
                     architecture: 'x86', bitness: '64' }),
  };
  hide(navigator, 'userAgentData', uaData);

  // --- 7. Permissions: automation answers notifications oddly --------------
  try {
    const origQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
    const patched = (params) => (
      params && params.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(params)
    );
    keepNative(patched, 'query');
    window.navigator.permissions.query = patched;
  } catch (e) {}

  // --- 8. WebGL vendor/renderer (software renderers scream "headless") -----
  const patchGL = (Ctor) => {
    if (!Ctor) return;
    const orig = Ctor.prototype.getParameter;
    const patched = function (p) {
      if (p === 37445) return P.webgl_vendor;
      if (p === 37446) return P.webgl_renderer;
      return orig.call(this, p);
    };
    keepNative(patched, 'getParameter');
    Ctor.prototype.getParameter = patched;
  };
  patchGL(window.WebGLRenderingContext);
  patchGL(window.WebGL2RenderingContext);

  // --- 9. Geometry: a headless window has no browser chrome ----------------
  // Real Chrome: outerHeight > innerHeight by the toolbar, and the window
  // fits inside the screen. Headless reports outerHeight === innerHeight.
  hide(window, 'outerWidth', V.width);
  hide(window, 'outerHeight', V.height + P.chrome_height);
  hide(window, 'screenX', 0);
  hide(window, 'screenY', 0);
  hide(window, 'devicePixelRatio', 1);
  hide(screen, 'width', P.screen.width);
  hide(screen, 'height', P.screen.height);
  hide(screen, 'availWidth', P.screen.width);
  hide(screen, 'availHeight', Math.max(P.screen.height - 40, V.height + P.chrome_height));
  hide(screen, 'colorDepth', 24);
  hide(screen, 'pixelDepth', 24);
})();
"""


def init_script(prof: dict, viewport: dict) -> str:
    """Render the init script with the profile and viewport baked in."""
    payload = dict(prof)
    payload.setdefault("languages", ["en-US", "en"])
    return _INIT_SCRIPT.replace(
        "__PROFILE__", json.dumps(payload, ensure_ascii=False)
    ).replace(
        "__VIEWPORT__", json.dumps(viewport, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# Human-like interaction timing
# ---------------------------------------------------------------------------
def _range(cfg: dict, key: str, default: tuple[int, int]) -> tuple[int, int]:
    raw = cfg.get(key) or list(default)
    try:
        lo, hi = int(raw[0]), int(raw[1])
    except (TypeError, ValueError, IndexError):
        lo, hi = default
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def rand_range(cfg: dict, key: str, default: tuple[int, int]) -> float:
    lo, hi = _range(cfg, key, default)
    return random.uniform(lo, hi) / 1000.0


def human_pause(hum: dict) -> None:
    """Random idle between actions. Machine-cadence requests are a signal too."""
    if not hum.get("enabled") or not hum.get("pause_ms", True):
        return
    time.sleep(rand_range(hum, "pause_ms", (300, 1200)))


def char_delay(hum: dict) -> float:
    """Per-keystroke delay in **seconds**, ready to hand to time.sleep.

    Config is expressed in milliseconds (`typing_delay_ms`) because that is how
    humans write these numbers; every sleep in this codebase is in seconds, so
    the conversion happens here, in one place. Do not divide by 1000 at the
    call site — that is how you get 85-second keystrokes.
    """
    if not hum.get("enabled"):
        return int(hum.get("typing_delay_ms", [40, 130])[0]) / 1000.0
    lo, hi = _range(hum, "typing_delay_ms", (40, 130))
    return random.uniform(lo, hi) / 1000.0


def human_mouse_move(page, x: float, y: float, hum: dict) -> None:
    """Move in steps with slight jitter instead of teleporting the cursor."""
    if not hum.get("enabled"):
        page.mouse.move(x, y)
        return
    lo, hi = _range(hum, "mouse_steps", (8, 25))
    steps = random.randint(lo, hi)
    # Drift a few px off target, then arrive: a perfectly straight jump looks
    # synthetic to trajectory-based models.
    jx = x + random.uniform(-3, 3)
    jy = y + random.uniform(-3, 3)
    page.mouse.move(jx, jy, steps=max(1, steps // 2))
    page.mouse.move(x, y, steps=max(1, steps - steps // 2))


# ---------------------------------------------------------------------------
# Detection probe: report what a fingerprinting script would actually see.
# ---------------------------------------------------------------------------
PROBE_JS = r"""
() => {
  const out = {};
  const safe = (fn, fallback = null) => { try { return fn(); } catch (e) { return fallback; } };

  out.userAgent = navigator.userAgent;
  out.headlessUA = /HeadlessChrome|headless/i.test(navigator.userAgent);
  out.webdriver = navigator.webdriver;
  out.webdriverIn = safe(() => 'webdriver' in navigator);
  out.chrome = safe(() => !!window.chrome);
  out.plugins = safe(() => navigator.plugins.length, 0);
  out.languages = safe(() => Array.from(navigator.languages || []));
  out.platform = navigator.platform;
  out.hardwareConcurrency = navigator.hardwareConcurrency;
  out.deviceMemory = safe(() => navigator.deviceMemory);
  out.uaDataPlatform = safe(() => navigator.userAgentData && navigator.userAgentData.platform);

  out.outerMinusInner = safe(() => window.outerHeight - window.innerHeight);
  out.screen = safe(() => [screen.width, screen.height]);
  out.devicePixelRatio = safe(() => window.devicePixelRatio);

  out.cdc = safe(() => (
    Object.getOwnPropertyNames(window).filter((k) => /^(cdc_|\$cdc_)/.test(k)).length +
    Object.getOwnPropertyNames(document).filter((k) => /^(cdc_|\$cdc_)/.test(k)).length
  ), -1);

  out.permToStringNative = safe(() => (navigator.permissions.query + '').indexOf('native code') >= 0);

  const gl = safe(() => document.createElement('canvas').getContext('webgl'));
  out.webglVendor = safe(() => {
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null;
  });
  out.webglRenderer = safe(() => {
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null;
  });

  // Does the UA's claimed platform agree with Client Hints? A mismatch here is
  // worse than no patch at all.
  out.platformConsistent = (() => {
    const ua = navigator.userAgent;
    const p = out.uaDataPlatform || '';
    if (/Windows/.test(ua)) return p === 'Windows';
    if (/Macintosh/.test(ua)) return p === 'macOS';
    if (/X11; Linux|Linux x86_64/.test(ua)) return p === 'Linux';
    return true;
  })();

  out.webglSoftware = /SwiftShader|llvmpipe|Software|Mesa OffScreen/i.test(out.webglRenderer || '');
  return out;
}
"""


def score_probe(p: dict) -> tuple[list[tuple[str, bool, str]], int, int]:
    """Turn raw probe output into (checks, passed, total).

    Each entry is (name, ok, observed-value-as-string).
    """
    def v(x):
        return "(none)" if x is None else str(x)

    checks: list[tuple[str, bool, str]] = [
        ("navigator.webdriver", p.get("webdriver") in (None, False, "undefined"), v(p.get("webdriver"))),
        ("'webdriver' in navigator", p.get("webdriverIn") is False, v(p.get("webdriverIn"))),
        ("window.chrome present", bool(p.get("chrome")), v(p.get("chrome"))),
        ("no headless UA token", not p.get("headlessUA"), "clean" if not p.get("headlessUA") else "HeadlessChrome in UA"),
        ("plugins populated", (p.get("plugins") or 0) > 0, f"{v(p.get('plugins'))} plugins"),
        ("languages set", bool(p.get("languages")), ",".join(p.get("languages") or []) or "(none)"),
        ("hardwareConcurrency", (p.get("hardwareConcurrency") or 0) >= 2, v(p.get("hardwareConcurrency"))),
        ("deviceMemory", (p.get("deviceMemory") or 0) >= 2, v(p.get("deviceMemory"))),
        ("window chrome offset", (p.get("outerMinusInner") or 0) > 0,
         f"outer-inner={v(p.get('outerMinusInner'))}px"),
        ("no cdc_ markers", p.get("cdc") == 0, f"{v(p.get('cdc'))} markers"),
        ("permissions toString native", p.get("permToStringNative") is True, v(p.get("permToStringNative"))),
        ("platform consistency", p.get("platformConsistent") is True,
         f"platform={v(p.get('platform'))} uaData={v(p.get('uaDataPlatform'))}"),
        ("WebGL not software", not p.get("webglSoftware"), v(p.get("webglRenderer"))[:70]),
    ]
    passed = sum(1 for _, ok, _ in checks if ok)
    return checks, passed, len(checks)


__all__ = [
    "PROFILES", "DEFAULT_PROFILE", "profile_names", "get_profile",
    "launch_kwargs", "context_kwargs", "init_script",
    "human_pause", "char_delay", "human_mouse_move",
    "PROBE_JS", "score_probe",
]
