/**
 * Copy text to the clipboard with graceful degradation.
 *
 * navigator.clipboard.writeText only works in secure contexts (HTTPS /
 * localhost). Real fleet deployments often serve the hub UI over plain HTTP
 * on an internal IP, where the modern API is blocked by the browser. Fall
 * back to the legacy execCommand trick; last resort, prompt the user so they
 * can copy by hand.
 *
 * Returns true when the text landed on the clipboard; false means the
 * prompt fallback was shown instead (the caller should not flash "Copied").
 */
export async function copyText(
  text: string,
  promptLabel = "Copy this text:",
): Promise<boolean> {
  // Modern path — only works on HTTPS or http://localhost.
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      console.warn("clipboard.writeText failed, falling back", e);
    }
  }

  // Legacy fallback. Deprecated but still supported across browsers
  // and (crucially) works on plain-HTTP origins.
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.opacity = "0";
    ta.style.pointerEvents = "none";
    ta.setAttribute("readonly", "");
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    if (ok) return true;
  } catch (e) {
    console.warn("execCommand copy fallback failed", e);
  }

  window.prompt(promptLabel, text);
  return false;
}
