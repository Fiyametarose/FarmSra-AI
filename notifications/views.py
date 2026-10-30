from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.urls import reverse
from users.models import User
from .models import Notification

@login_required
def inbox(request):
    notifications = list(request.user.notifications.all())
    from expert_reviews.models import ExpertReferral

    referral_events = {
        Notification.EventType.EXPERT_ASSIGNED,
        Notification.EventType.NEW_MESSAGE,
        Notification.EventType.COORDINATOR_REQUEST,
    }
    referral_ids = [n.metadata.get('referral_id') for n in notifications if n.event_type in referral_events and n.metadata.get('referral_id')]
    referrals_by_id = {}
    if referral_ids:
        for ref in ExpertReferral.objects.filter(id__in=referral_ids):
            referrals_by_id[str(ref.id)] = ref

    for n in notifications:
        n.action_url = None
        if n.event_type == 'expert_assigned':
            rid = str(n.metadata.get('referral_id')) if n.metadata.get('referral_id') else None
            ref = referrals_by_id.get(rid)
            n.is_revoked = ref is not None and ref.state == ExpertReferral.State.REVOKED
            n.is_reassigned = ref is not None and ref.assigned_expert_id != request.user.id
            n.is_actionable = ref is not None and not n.is_revoked and not n.is_reassigned
            if n.is_actionable:
                n.action_url = reverse('expert-case-detail', args=[ref.id])
        elif n.event_type == Notification.EventType.NEW_MESSAGE:
            rid = str(n.metadata.get('referral_id')) if n.metadata.get('referral_id') else None
            ref = referrals_by_id.get(rid)
            active = ref is not None and ref.state in (ExpertReferral.State.ASSIGNED, ExpertReferral.State.REVIEWED)
            if active and request.user.id == ref.diagnosis.farmer_id:
                n.action_url = reverse('case-detail', args=[ref.diagnosis_id])
            elif active and request.user.id == ref.assigned_expert_id:
                n.action_url = reverse('expert-case-detail', args=[ref.id])
            n.is_actionable = n.action_url is not None
        elif n.event_type == Notification.EventType.COORDINATOR_REQUEST:
            rid = str(n.metadata.get('referral_id')) if n.metadata.get('referral_id') else None
            ref = referrals_by_id.get(rid)
            n.is_actionable = request.user.role == User.Role.ADMINISTRATOR and request.user.is_staff and ref is not None and ref.state == ExpertReferral.State.REQUESTED
            if n.is_actionable:
                n.action_url = reverse('admin:expert_reviews_expertreferral_change', args=[ref.id])
        else:
            n.is_actionable = True

    base_template = 'admin/base_site.html' if request.user.role == User.Role.ADMINISTRATOR and request.user.is_staff else 'base.html'
    return render(request, 'notifications/inbox.html', {'notifications': notifications, 'base_template': base_template})

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
