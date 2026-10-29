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

const cropSelect = document.getElementById("id_registered_crop");
const locationInput = document.getElementById("id_location");
const locationSourceInput = document.getElementById("id_location_source");
const locationStatus = document.getElementById("location-status");
const cropLocationsElement = document.getElementById("crop-locations");
const farmLocationElement = document.getElementById("farm-location");
const preserveLocationElement = document.getElementById("preserve-location-value");

if (cropSelect && locationInput && cropLocationsElement && farmLocationElement && preserveLocationElement) {
  const cropLocations = JSON.parse(cropLocationsElement.textContent);
  const farmLocation = JSON.parse(farmLocationElement.textContent);
  const preserveLocation = JSON.parse(preserveLocationElement.textContent);
  let suggestedLocation = "";

  if (preserveLocation) locationInput.dataset.userEdited = "true";

  const applySuggestedLocation = () => {
    if (locationInput.dataset.userEdited === "true") return;
    suggestedLocation = cropLocations[cropSelect.value] || farmLocation || "";
    locationInput.value = suggestedLocation;
  };

  locationInput.addEventListener("input", () => {
    locationInput.dataset.userEdited = "true";
    if (locationSourceInput) locationSourceInput.value = "";
  });
  cropSelect.addEventListener("change", applySuggestedLocation);
  applySuggestedLocation();
}

const deviceLocationButton = document.getElementById("use-device-location");
if (deviceLocationButton && locationInput) {
  deviceLocationButton.addEventListener("click", () => {
    if (!navigator.geolocation) {
      locationStatus.textContent = deviceLocationButton.dataset.unsupported;
      return;
    }
    locationStatus.textContent = deviceLocationButton.dataset.requesting;
    navigator.geolocation.getCurrentPosition(
      (position) => {
        locationInput.value = `${position.coords.latitude}, ${position.coords.longitude}`;
        if (locationSourceInput) locationSourceInput.value = "browser_geolocation";
        locationInput.dataset.userEdited = "true";
        locationStatus.textContent = deviceLocationButton.dataset.added;
      },
      () => {
        locationStatus.textContent = deviceLocationButton.dataset.unavailable;
      }
    );
  });
}
