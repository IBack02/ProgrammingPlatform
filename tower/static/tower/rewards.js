(() => {
  const form = document.getElementById("batch-award");
  if (!form) return;
  const boxes = [...form.querySelectorAll('[name="student_ids"]')];
  const all = document.getElementById("reward-select-all");
  const summary = form.querySelector("[data-reward-summary]");
  const submit = form.querySelector("[data-reward-submit]");
  const amount = form.querySelector('[name="amount"]');
  const update = () => {
    const count = boxes.filter(box => box.checked).length;
    const value = Number(amount.value);
    all.checked = count > 0 && count === boxes.length;
    all.indeterminate = count > 0 && count < boxes.length;
    summary.textContent = count ? summary.dataset.template.replace("{count}", count).replace("{total}", (count * (Number.isSafeInteger(value) && value > 0 ? value : 0)).toLocaleString(document.documentElement.lang)) : summary.dataset.empty;
    submit.disabled = count === 0;
  };
  all.addEventListener("change", () => { boxes.forEach(box => { box.checked = all.checked; }); update(); });
  form.addEventListener("input", event => { if (event.target !== all) update(); });
  form.addEventListener("submit", () => { if (form.checkValidity()) submit.disabled = true; });
  window.addEventListener("pageshow", update);
  document.querySelector('a[href="#journal-access"]')?.addEventListener("click", () => { document.getElementById("journal-access").open = true; });
  update();
})();
