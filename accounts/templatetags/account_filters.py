from django import template

register = template.Library()


@register.filter(name="split")
def split_string(value, arg="."):
    return value.split(arg)


@register.filter(name="strip")
def strip_string(value, arg=None):
    if arg:
        return value.strip(arg)
    return value.strip()
