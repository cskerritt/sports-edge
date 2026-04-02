from django import template

from subscriptions.models import TIER_RANK

register = template.Library()


class IfTierNode(template.Node):
    def __init__(self, required_tier, nodelist_true, nodelist_false):
        self.required_tier = required_tier
        self.nodelist_true = nodelist_true
        self.nodelist_false = nodelist_false

    def render(self, context):
        request = context.get("request")
        user_tier = getattr(request, "subscription_tier", "FREE") if request else "FREE"
        user_rank = TIER_RANK.get(user_tier, 0)
        required_rank = TIER_RANK.get(self.required_tier, 0)

        if user_rank >= required_rank:
            return self.nodelist_true.render(context)
        return self.nodelist_false.render(context)


@register.tag("if_tier")
def do_if_tier(parser, token):
    """Usage: {% if_tier "PRO" %} ... {% else_tier %} ... {% endif_tier %}"""
    bits = token.split_contents()
    if len(bits) != 2:
        raise template.TemplateSyntaxError("if_tier requires one argument: the tier name")

    required_tier = bits[1].strip('"').strip("'")

    nodelist_true = parser.parse(("else_tier", "endif_tier"))
    token = parser.next_token()
    if token.contents == "else_tier":
        nodelist_false = parser.parse(("endif_tier",))
        parser.delete_first_token()
    else:
        nodelist_false = template.NodeList()

    return IfTierNode(required_tier, nodelist_true, nodelist_false)


@register.simple_tag(takes_context=True)
def user_tier(context):
    """Return the current user's subscription tier string."""
    request = context.get("request")
    return getattr(request, "subscription_tier", "FREE") if request else "FREE"


@register.simple_tag(takes_context=True)
def tier_badge_class(context):
    """Return Tailwind classes for the user's tier badge."""
    request = context.get("request")
    tier = getattr(request, "subscription_tier", "FREE") if request else "FREE"
    return {
        "FREE": "bg-slate-700 text-slate-300",
        "PRO": "bg-blue-600 text-white",
        "ELITE": "bg-purple-600 text-white",
    }.get(tier, "bg-slate-700 text-slate-300")
