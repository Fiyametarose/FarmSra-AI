from .models import AdvisoryVersion, RecommendationAction
from .workflow import NOT_APPLICABLE

TRACKED_SECTIONS = ("immediate_actions", "prevention", "monitoring", "treatment_considerations")


def ensure_actions(version, farmer):
    """Create Not Started trackers for a saved advisory version; never change existing ones."""
    if version.status != AdvisoryVersion.Status.SUCCEEDED:
        return
    for key in TRACKED_SECTIONS:
        text = (version.sections or {}).get(key, "").strip()
        if text and text not in NOT_APPLICABLE.values():
            RecommendationAction.objects.get_or_create(
                advisory_version=version, farmer=farmer, section_key=key,
                defaults={"recommendation": text},
            )
