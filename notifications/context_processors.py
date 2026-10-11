def unread_notifications(request):
    if request.user.is_authenticated:
        return {'unread_notifications_count': request.user.notifications.filter(read_at__isnull=True).count()}
    return {'unread_notifications_count': 0}
