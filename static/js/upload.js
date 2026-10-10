const input = document.querySelector('input[type="file"]');
const preview = document.getElementById("image-preview");
const placeholder = document.getElementById("preview-placeholder");
input.addEventListener("change", () => {
  const file = input.files[0];
  if (!file) return;
  preview.src = URL.createObjectURL(file);
  preview.hidden = false;
  placeholder.hidden = true;
});
