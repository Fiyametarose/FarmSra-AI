from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils import timezone
from .models import Notification

@login_required
def inbox(request):
    notifications = list(request.user.notifications.all())
    from expert_reviews.models import ExpertReferral

    # Pre-fetch referrals for expert_assigned notifications
    referral_ids = [n.metadata.get('referral_id') for n in notifications if n.event_type == 'expert_assigned' and n.metadata.get('referral_id')]
    referrals_by_id = {}
    if referral_ids:
        for ref in ExpertReferral.objects.filter(id__in=referral_ids):
            referrals_by_id[str(ref.id)] = ref

    for n in notifications:
        if n.event_type == 'expert_assigned':
            rid = str(n.metadata.get('referral_id')) if n.metadata.get('referral_id') else None
            ref = referrals_by_id.get(rid)
            n.is_revoked = ref is not None and ref.state == ExpertReferral.State.REVOKED
            n.is_reassigned = ref is not None and ref.assigned_expert_id != request.user.id
            n.is_actionable = ref is not None and not n.is_revoked and not n.is_reassigned
        else:
            n.is_actionable = True

    return render(request, 'notifications/inbox.html', {'notifications': notifications})

@login_required
@require_POST
def mark_read(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    if not notification.is_read():
        notification.read_at = timezone.now()
        notification.save(update_fields=['read_at'])
    return redirect('notifications:inbox')

@login_required
@require_POST
def mark_all_read(request):
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect('notifications:inbox')
