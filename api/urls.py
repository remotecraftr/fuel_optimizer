from django.urls import path
from .views import ApiIndexView, HealthView, RouteView, MapView, MapDataView

urlpatterns = [
    path('', ApiIndexView.as_view()),
    path('health/', HealthView.as_view()),
    path('route/', RouteView.as_view()),
    path('map/<str:map_id>/', MapView.as_view()),
    path('map/<str:map_id>/data/', MapDataView.as_view()),
]