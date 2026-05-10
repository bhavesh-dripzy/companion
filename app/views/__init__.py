from django.http import JsonResponse
from django.shortcuts import render


def health_check(_request):
    return JsonResponse({"status": "ok"})


def food_ui(request):
    return render(request, "app/food_ui.html")
