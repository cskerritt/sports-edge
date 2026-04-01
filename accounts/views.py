from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import render, redirect
from bankroll.models import UserBankrollSettings
from .forms import RegisterForm, PreferencesForm
from .models import UserProfile


def register_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard:index")
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Welcome to Sports Edge! Set up your preferences below.")
            return redirect("accounts:preferences")
    else:
        form = RegisterForm()
    return render(request, "accounts/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard:index")
    if request.method == "POST":
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            next_url = request.GET.get("next", "dashboard:index")
            return redirect(next_url)
    else:
        form = AuthenticationForm()
    return render(request, "accounts/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("accounts:login")


@login_required
def preferences_view(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    settings = UserBankrollSettings.get_for_user(request.user)

    if request.method == "POST":
        form = PreferencesForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            # Update bankroll settings
            try:
                settings.edge_threshold = float(request.POST.get("edge_threshold", settings.edge_threshold))
                settings.kelly_fraction = float(request.POST.get("kelly_fraction", settings.kelly_fraction))
                settings.save()
            except (ValueError, TypeError):
                pass
            messages.success(request, "Preferences saved.")
            if request.htmx:
                return render(request, "accounts/_preferences_saved.html")
            return redirect("accounts:preferences")
    else:
        form = PreferencesForm(instance=profile)

    return render(request, "accounts/preferences.html", {
        "form": form,
        "settings": settings,
    })
