"""Хендлеры бота. admin подключается первым (у его сообщений свой фильтр)."""

from aiogram import Router

from . import admin, profile, user


def build_root_router() -> Router:
    root = Router()
    root.include_router(admin.router)
    root.include_router(profile.router)
    root.include_router(user.router)
    return root
