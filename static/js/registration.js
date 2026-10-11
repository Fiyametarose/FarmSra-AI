const steps = [...document.querySelectorAll(".form-step")];
const stepCopy = document.getElementById("step-copy");
function showStep(index) {
  steps.forEach((step, i) => step.classList.toggle("hidden", i !== index));
  if (stepCopy) {
    stepCopy.textContent = index === 0
      ? (stepCopy.dataset.step1 || stepCopy.textContent)
      : (stepCopy.dataset.step2 || stepCopy.textContent);
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}
document.getElementById("registration-next").addEventListener("click", () => {
  const required = steps[0].querySelectorAll("input");
  if ([...required].every((field) => field.reportValidity())) showStep(1);
});
document.getElementById("registration-back").addEventListener("click", () => showStep(0));
if (document.querySelector(".errorlist")) {
  const locationNames = ["country", "region", "subcounty", "ward", "town", "locality", "terms_accepted"];
  const hasLocationError = locationNames.some((name) => document.getElementById(`id_${name}`)?.closest("label")?.querySelector(".errorlist"));
  if (hasLocationError) showStep(1);
}
