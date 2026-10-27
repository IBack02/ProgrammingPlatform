// Revalidate the server-side lock when a browser restores a cached journal page.
window.addEventListener("pageshow", (event) => {
  if (event.persisted) window.location.reload();
});
