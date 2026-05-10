from django.urls import path

from app.views import health_check
from app.views.food_agent import food_agent_full
from app.views.reminders import pending_reminders, respond_reminder
from app.views.send_message import send_whatsapp_message

urlpatterns = [
    path("", health_check, name="health-check"),
    path("food/query/", food_agent_full, name="food-agent-full"),
    path("send-message/", send_whatsapp_message, name="send-whatsapp-message"),
    path("reminders/pending/", pending_reminders, name="pending-reminders"),
    path("reminders/respond/", respond_reminder, name="respond-reminder"),
]
