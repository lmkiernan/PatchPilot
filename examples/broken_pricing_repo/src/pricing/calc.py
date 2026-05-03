def compute_discount(price, discount):
    return price * discount.percent


def apply_tax(price, tax_rate):
    return price + (price * tax_rate)


def final_price(price, discount=None, tax_rate=0.08):
    discounted = compute_discount(price, discount)
    return apply_tax(discounted, tax_rate)
