from django.http import HttpResponse
from django.shortcuts import render


def home(request):
    return render(request, 'tracker/home.html')


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")
