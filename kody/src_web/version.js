async function loadKodyVersion() {
  const targets = document.querySelectorAll("[data-kody-version]");
  try {
    const response = await fetch("/VERSION.txt", { cache: "no-cache" });
    if (!response.ok) {
      throw new Error(`version file returned ${response.status}`);
    }
    const version = (await response.text()).trim();
    if (!version) {
      throw new Error("version file is empty");
    }
    targets.forEach((target) => {
      target.textContent = version;
    });
  } catch (error) {
    console.error("Unable to load Kody version:", error);
    targets.forEach((target) => {
      target.textContent = "Version unavailable";
    });
  }
}

loadKodyVersion();
