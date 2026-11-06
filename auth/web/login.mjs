// The sign-in page: the password is encrypted with the server's RSA key (PKCS#1 v1.5) before it is posted, since
// the site may be served over plain HTTP, where browsers do not offer Web Crypto. BigInt does the arithmetic.

const $ = (selector) => document.querySelector(selector);
let mode = "account";

function bytesToBigInt(bytes) {
  let hex = "";
  for (const b of bytes) hex += b.toString(16).padStart(2, "0");
  return BigInt(`0x${hex || "0"}`);
}

function modPow(base, exponent, modulus) {
  let result = 1n;
  base %= modulus;
  while (exponent > 0n) {
    if (exponent & 1n) result = (result * base) % modulus;
    exponent >>= 1n;
    base = (base * base) % modulus;
  }
  return result;
}

/** RSA PKCS#1 v1.5 (type 2) encryption of a UTF-8 string -> base64. */
export function encrypt(text, key) {
  const n = BigInt(`0x${key.n}`);
  const e = BigInt(`0x${key.e}`);
  const size = Math.ceil(key.bits / 8);
  const message = new TextEncoder().encode(text);
  if (message.length > size - 11) throw new Error("The password is too long");
  const padding = new Uint8Array(size - message.length - 3);
  crypto.getRandomValues(padding);
  for (let i = 0; i < padding.length; i += 1) {
    while (padding[i] === 0) padding[i] = crypto.getRandomValues(new Uint8Array(1))[0];
  }
  const block = new Uint8Array(size);
  block[1] = 2;
  block.set(padding, 2);
  block.set(message, padding.length + 3);
  const cipher = modPow(bytesToBigInt(block), e, n).toString(16).padStart(size * 2, "0");
  let binary = "";
  for (let i = 0; i < cipher.length; i += 2) binary += String.fromCharCode(parseInt(cipher.slice(i, i + 2), 16));
  return btoa(binary);
}

function setMode(next) {
  mode = next;
  for (const tab of document.querySelectorAll("[data-mode]")) tab.setAttribute("aria-selected", String(tab.dataset.mode === mode));
  $('[data-for="account"]').hidden = mode !== "account";
  $("#auth-user").required = mode === "account";
  $('[data-for="password"]').hidden = mode !== "password";
  $("#auth-password").autocomplete = mode === "account" ? "current-password" : "off";
  $("#auth-error").hidden = true;
  (mode === "account" && !$("#auth-user").value ? $("#auth-user") : $("#auth-password")).focus();
}

async function submit(event) {
  event.preventDefault();
  const button = $("#auth-submit");
  const error = $("#auth-error");
  button.disabled = true;
  error.hidden = true;
  try {
    const key = await (await fetch("/auth/api/key", { cache: "no-store" })).json();
    const secret = JSON.stringify({ u: mode === "account" ? $("#auth-user").value.trim() : "", p: $("#auth-password").value, nonce: key.nonce });
    const response = await fetch("/auth/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, cipher: encrypt(secret, key), remember: $("#auth-remember").checked,
        next: new URLSearchParams(location.search).get("next") || "/" }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Sign-in failed (${response.status})`);
    location.replace(payload.next || "/");
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
    $("#auth-password").select();
  } finally {
    button.disabled = false;
  }
}

for (const tab of document.querySelectorAll("[data-mode]")) tab.addEventListener("click", () => setMode(tab.dataset.mode));
$("#auth-form").addEventListener("submit", submit);
setMode(localStorage.getItem("omics-canvas:sign-in-mode") === "password" ? "password" : "account");
for (const tab of document.querySelectorAll("[data-mode]")) {
  tab.addEventListener("click", () => { try { localStorage.setItem("omics-canvas:sign-in-mode", tab.dataset.mode); } catch { /* ok */ } });
}
