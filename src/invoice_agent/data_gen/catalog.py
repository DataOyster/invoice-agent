"""Hand-written, invented master data: suppliers, products and contract clause templates.

Nothing here is random, so a human can review it once and check that no name belongs to a real
company. Organisation numbers ("000000-xxxx") and IBANs (check digits "00") are impossible on
purpose, so they can never collide with a real one.
"""

from __future__ import annotations

from invoice_agent.data_gen.models import Product, Supplier

BUYER = {
    "name": "Restaurang Ljuslyktan AB",
    "org_number": "000000-0000",
    "address": "Fiktiva gatan 1, 111 11 Stockholm",
}


def _supplier(
    n: int,
    name: str,
    category: str,
    language: str,
    layout: str,
    prefix: str,
    address: str,
    role: str = "supplier",
) -> Supplier:
    return Supplier(
        supplier_id=f"S{n:02d}",
        name=name,
        category=category,
        role=role,
        language=language,
        layout=layout,
        org_number=f"000000-{n:04d}",
        iban=f"SE00 9999 0000 0000 0000 {n:04d}",
        address=address,
        invoice_prefix=prefix,
    )


SUPPLIERS: tuple[Supplier, ...] = (
    _supplier(
        1, "Kallgrund Fiskhandel AB", "fish", "sv", "A", "KF", "Bryggvägen 3, 426 00 Göteborg"
    ),
    _supplier(
        2, "Grönhaga Odlarlag AB", "produce", "sv", "B", "GH", "Fältstigen 12, 745 00 Enköping"
    ),
    _supplier(
        3, "Mjölkudden Dairy AB", "dairy", "en", "C", "MU", "Ladugårdsvägen 7, 523 00 Ulricehamn"
    ),
    _supplier(
        4, "Dispensa Lindqvarn AB", "dry goods", "it", "A", "DL", "Kvarngatan 21, 214 00 Malmö"
    ),
    _supplier(5, "Ekbacka Butchery AB", "meat", "en", "B", "EB", "Slakterigatan 5, 703 00 Örebro"),
    _supplier(
        6, "Cantina Solsten AB", "drinks", "it", "C", "CS", "Källarvägen 9, 118 00 Stockholm"
    ),
    _supplier(
        7,
        "Hamnkajen Grossist AB",
        "produce and dairy",
        "sv",
        "A",
        "HK",
        "Kajgatan 40, 602 00 Norrköping",
        role="competitor",
    ),
)

# (product_id, canonical English name, Swedish, Italian, unit, price SEK excl. VAT,
#  kg per case or None, VAT category). English invoice text = canonical name.
_PRODUCT_ROWS: tuple[tuple, ...] = (
    # S01 fish
    ("P101", "Salmon fillet", "Laxfilé", "Filetto di salmone", "kg", 189.00, 5, "food"),
    ("P102", "Cod loin", "Torskrygg", "Dorso di merluzzo", "kg", 239.00, 4, "food"),
    (
        "P103",
        "Sea bass whole 1-2 kg",
        "Havsabborre hel 1-2 kg",
        "Branzino intero 1-2 kg",
        "kg",
        145.00,
        6,
        "food",
    ),
    ("P104", "Cooked shrimps", "Kokta räkor", "Gamberi cotti", "kg", 159.00, 5, "food"),
    ("P105", "Mussels", "Blåmusslor", "Cozze", "kg", 49.00, 10, "food"),
    ("P106", "Herring fillet", "Sillfilé", "Filetto di aringa", "kg", 79.00, 5, "food"),
    ("P107", "Arctic char fillet", "Rödingfilé", "Filetto di salmerino", "kg", 219.00, 4, "food"),
    ("P108", "Squid cleaned", "Bläckfisk rensad", "Calamari puliti", "kg", 129.00, 5, "food"),
    ("P109", "Pike-perch fillet", "Gösfilé", "Filetto di lucioperca", "kg", 289.00, 3, "food"),
    (
        "P110",
        "Smoked salmon sliced",
        "Rökt lax skivad",
        "Salmone affumicato a fette",
        "kg",
        299.00,
        2,
        "food",
    ),
    ("P111", "Fish stock", "Fiskfond", "Fumetto di pesce", "l", 59.00, None, "food"),
    (
        "P112",
        "Lumpfish roe 100 g",
        "Stenbitsrom 100 g",
        "Uova di lompo 100 g",
        "pcs",
        34.50,
        None,
        "food",
    ),
    # S02 produce
    ("P201", "Tomatoes", "Tomater", "Pomodori", "kg", 32.00, 6, "food"),
    ("P202", "Potatoes", "Potatis", "Patate", "kg", 9.50, 25, "food"),
    ("P203", "Yellow onions", "Gul lök", "Cipolle dorate", "kg", 11.00, 20, "food"),
    ("P204", "Lemons", "Citroner", "Limoni", "kg", 29.00, 15, "food"),
    ("P205", "Basil in pot", "Basilika i kruka", "Basilico in vaso", "pcs", 18.00, None, "food"),
    ("P206", "Romaine lettuce", "Romansallad", "Lattuga romana", "pcs", 14.50, None, "food"),
    ("P207", "Carrots", "Morötter", "Carote", "kg", 12.50, 10, "food"),
    ("P208", "Garlic", "Vitlök", "Aglio", "kg", 69.00, 5, "food"),
    ("P209", "Zucchini", "Zucchini", "Zucchine", "kg", 34.00, 5, "food"),
    ("P210", "Button mushrooms", "Champinjoner", "Funghi champignon", "kg", 59.00, 3, "food"),
    ("P211", "Parsley bunch", "Persilja knippe", "Prezzemolo mazzo", "pcs", 12.00, None, "food"),
    ("P212", "Apples", "Äpplen", "Mele", "kg", 24.00, 13, "food"),
    # S03 dairy
    ("P301", "Whole milk 3%", "Standardmjölk 3%", "Latte intero 3%", "l", 13.20, None, "food"),
    ("P302", "Whipping cream 36%", "Vispgrädde 36%", "Panna 36%", "l", 49.00, None, "food"),
    ("P303", "Unsalted butter", "Osaltat smör", "Burro non salato", "kg", 109.00, 10, "food"),
    (
        "P304",
        "Parmesan 24 months",
        "Parmesan 24 månader",
        "Parmigiano 24 mesi",
        "kg",
        289.00,
        4,
        "food",
    ),
    (
        "P305",
        "Mozzarella fior di latte",
        "Mozzarella fior di latte",
        "Mozzarella fior di latte",
        "kg",
        119.00,
        3,
        "food",
    ),
    (
        "P306",
        "Free-range eggs 30 pcs",
        "Frigående ägg 30 st",
        "Uova allevamento a terra 30 pz",
        "pcs",
        89.00,
        None,
        "food",
    ),
    (
        "P307",
        "Greek yoghurt 10%",
        "Grekisk yoghurt 10%",
        "Yogurt greco 10%",
        "kg",
        54.00,
        6,
        "food",
    ),
    (
        "P308",
        "Creme fraiche 34%",
        "Crème fraiche 34%",
        "Crème fraîche 34%",
        "l",
        58.00,
        None,
        "food",
    ),
    ("P309", "Aged hard cheese", "Lagrad hårdost", "Formaggio stagionato", "kg", 179.00, 4, "food"),
    ("P310", "Ricotta", "Ricotta", "Ricotta", "kg", 89.00, 2, "food"),
    ("P311", "Mascarpone", "Mascarpone", "Mascarpone", "kg", 99.00, 2, "food"),
    ("P312", "Quark", "Kvarg", "Quark", "kg", 45.00, 5, "food"),
    # S04 dry goods (includes non-food supplies at 25%)
    ("P401", "Spaghetti", "Spaghetti", "Spaghetti", "kg", 28.00, 6, "food"),
    ("P402", "Arborio rice", "Arborioris", "Riso Arborio", "kg", 42.00, 5, "food"),
    (
        "P403",
        "Extra virgin olive oil",
        "Extra jungfruolivolja",
        "Olio extravergine di oliva",
        "l",
        119.00,
        None,
        "food",
    ),
    ("P404", "Flour type 00", "Vetemjöl typ 00", "Farina tipo 00", "kg", 14.00, 25, "food"),
    (
        "P405",
        "Peeled tomatoes tin 2.5 kg",
        "Skalade tomater burk 2,5 kg",
        "Pomodori pelati latta 2,5 kg",
        "pcs",
        59.00,
        None,
        "food",
    ),
    ("P406", "Coarse sea salt", "Grovt havssalt", "Sale marino grosso", "kg", 19.00, 10, "food"),
    ("P407", "Balsamic vinegar", "Balsamvinäger", "Aceto balsamico", "l", 89.00, None, "food"),
    ("P408", "Sugar", "Strösocker", "Zucchero", "kg", 16.50, 10, "food"),
    (
        "P409",
        "Espresso coffee beans",
        "Kaffebönor espresso",
        "Caffè in grani espresso",
        "kg",
        249.00,
        None,
        "food",
    ),
    (
        "P410",
        "Dish detergent 5 l",
        "Diskmedel 5 l",
        "Detersivo piatti 5 l",
        "pcs",
        129.00,
        None,
        "non_food",
    ),
    (
        "P411",
        "Paper napkins 500 pcs",
        "Servetter 500 st",
        "Tovaglioli di carta 500 pz",
        "pcs",
        69.00,
        None,
        "non_food",
    ),
    (
        "P412",
        "Aluminium foil roll",
        "Aluminiumfolie rulle",
        "Rotolo di alluminio",
        "pcs",
        95.00,
        None,
        "non_food",
    ),
    # S05 meat
    ("P501", "Beef tenderloin", "Oxfilé", "Filetto di manzo", "kg", 529.00, 3, "food"),
    ("P502", "Minced beef 10%", "Nötfärs 10%", "Macinato di manzo 10%", "kg", 99.00, 5, "food"),
    ("P503", "Chicken breast", "Kycklingbröst", "Petto di pollo", "kg", 115.00, 5, "food"),
    ("P504", "Pork belly", "Fläsksida", "Pancetta di maiale", "kg", 89.00, 5, "food"),
    ("P505", "Lamb rack", "Lammracks", "Carré di agnello", "kg", 379.00, 3, "food"),
    ("P506", "Veal escalope", "Kalvschnitzel", "Scaloppina di vitello", "kg", 329.00, 3, "food"),
    ("P507", "Diced pancetta", "Tärnad pancetta", "Pancetta a cubetti", "kg", 189.00, 2, "food"),
    (
        "P508",
        "Sliced cured ham",
        "Lufttorkad skinka skivad",
        "Prosciutto crudo affettato",
        "kg",
        349.00,
        2,
        "food",
    ),
    ("P509", "Duck breast", "Ankbröst", "Petto d'anatra", "kg", 259.00, 4, "food"),
    ("P510", "Italian-style sausages", "Salsiccia", "Salsiccia", "kg", 139.00, 3, "food"),
    ("P511", "Beef stock", "Oxfond", "Brodo di manzo", "l", 49.00, None, "food"),
    ("P512", "Sliced bacon", "Bacon skivat", "Bacon a fette", "kg", 119.00, 2, "food"),
    # S06 drinks (alcohol only, 25%)
    (
        "P601",
        "Red wine Sangiovese 75 cl",
        "Rött vin Sangiovese 75 cl",
        "Vino rosso Sangiovese 75 cl",
        "bottle",
        89.00,
        None,
        "alcohol",
    ),
    (
        "P602",
        "White wine Vermentino 75 cl",
        "Vitt vin Vermentino 75 cl",
        "Vino bianco Vermentino 75 cl",
        "bottle",
        85.00,
        None,
        "alcohol",
    ),
    (
        "P603",
        "Sparkling wine 75 cl",
        "Mousserande vin 75 cl",
        "Spumante 75 cl",
        "bottle",
        79.00,
        None,
        "alcohol",
    ),
    (
        "P604",
        "Nebbiolo reserve 75 cl",
        "Nebbiolo reserva 75 cl",
        "Nebbiolo riserva 75 cl",
        "bottle",
        349.00,
        None,
        "alcohol",
    ),
    (
        "P605",
        "Lager 5.2% 33 cl",
        "Ljus lager 5,2% 33 cl",
        "Birra lager 5,2% 33 cl",
        "bottle",
        14.90,
        None,
        "alcohol",
    ),
    (
        "P606",
        "IPA 6.5% 33 cl",
        "IPA 6,5% 33 cl",
        "Birra IPA 6,5% 33 cl",
        "bottle",
        19.90,
        None,
        "alcohol",
    ),
    ("P607", "Grappa 70 cl", "Grappa 70 cl", "Grappa 70 cl", "bottle", 289.00, None, "alcohol"),
    (
        "P608",
        "Lemon liqueur 70 cl",
        "Citronlikör 70 cl",
        "Liquore al limone 70 cl",
        "bottle",
        219.00,
        None,
        "alcohol",
    ),
    ("P609", "Gin 70 cl", "Gin 70 cl", "Gin 70 cl", "bottle", 279.00, None, "alcohol"),
    (
        "P610",
        "House red wine bag-in-box 3 l",
        "Husets röda vin bag-in-box 3 l",
        "Vino rosso della casa bag-in-box 3 l",
        "pcs",
        199.00,
        None,
        "alcohol",
    ),
    (
        "P611",
        "Herbal bitter 70 cl",
        "Örtbitter 70 cl",
        "Amaro alle erbe 70 cl",
        "bottle",
        239.00,
        None,
        "alcohol",
    ),
    (
        "P612",
        "Rose wine 75 cl",
        "Rosévin 75 cl",
        "Vino rosato 75 cl",
        "bottle",
        82.00,
        None,
        "alcohol",
    ),
)

PRODUCTS: tuple[Product, ...] = tuple(
    Product(
        product_id=pid,
        supplier_id=f"S0{pid[1]}",
        name=en,
        names={"sv": sv, "en": en, "it": it},
        unit=unit,
        pack_weight_kg=pack,
        vat_category=vat,
    )
    for pid, en, sv, it, unit, _price, pack, vat in _PRODUCT_ROWS
)

BASE_PRICES: dict[str, float] = {row[0]: row[5] for row in _PRODUCT_ROWS}

# Suppliers whose price list changes on 2026-07-01, so "price valid on the delivery date" is tested.
PRICE_CHANGE_SUPPLIERS = ("S01", "S05")

# Products the competitor (S07) also sells; feeds optional task 13 only.
COMPETITOR_PRODUCTS = ("P201", "P202", "P203", "P207", "P303")

# Contract clauses. Two wordings per topic; the generator picks one per supplier with the seed.
# The wording avoids invoice vocabulary on purpose ("consignment", "remuneration", "net weight"),
# so retrieval is not a trivial keyword match.
CLAUSE_TOPICS: tuple[tuple[str, str, tuple[str, str]], ...] = (
    (
        "scope",
        "Scope of agreement",
        (
            (
                "This agreement governs the supply of {category} by {supplier} to {buyer}. It applies to "
                "every order placed during the calendar year 2026."
            ),
            (
                "{supplier} undertakes to deliver {category} to {buyer} on the terms below for all "
                "orders made in 2026."
            ),
        ),
    ),
    (
        "price_lock",
        "Fixed prices",
        (
            (
                "The prices set out in Annex A are fixed for each stated validity period. {supplier} may "
                "not charge any remuneration above the Annex A price applicable on the day the goods are "
                "handed over. A change of price requires 30 days' written notice and a new Annex."
            ),
            (
                "Agreed remuneration per item follows Annex A and is locked for the period printed "
                "there. The price that counts is the one in force on the day of hand-over, not the day "
                "of billing. Increases need a revised Annex signed by both parties."
            ),
        ),
    ),
    (
        "delivery",
        "Deliveries and acceptance",
        (
            (
                "Goods are delivered on {delivery_days}. The quantities acknowledged on the signed "
                "consignment receipt are binding; only those quantities may be billed."
            ),
            (
                "Consignments arrive on {delivery_days}. {buyer} signs a receipt on arrival and the "
                "receipted amounts are the only amounts that may be charged."
            ),
        ),
    ),
    (
        "units",
        "Units of measure",
        (
            (
                "Items in Annex A marked per kilogram are charged by net weight. Cases and boxes are "
                "packaging only and never a unit of charge for such items."
            ),
            (
                "Where Annex A states a price per kilogram, billing is by net weight; the number of "
                "cases or crates is irrelevant for the amount due."
            ),
        ),
    ),
    (
        "invoicing",
        "Billing and duplicates",
        (
            (
                "Each consignment is billed once, with a unique serial number. A document repeating an "
                "earlier serial number or an earlier billing for the same day and amount is void."
            ),
            (
                "Every bill carries its own serial number and covers one consignment only. Repeated "
                "bills for an already billed consignment are not payable."
            ),
        ),
    ),
    (
        "credit",
        "Credit notes",
        (
            (
                "Short deliveries, damaged or rejected goods are compensated by a credit note referring "
                "to the original bill, issued within 14 days."
            ),
            (
                "If goods are missing, spoiled or refused, {supplier} issues a credit note that cites "
                "the original bill. No other form of compensation applies."
            ),
        ),
    ),
    (
        "minimum_order",
        "Minimum order",
        (
            "The minimum order value is {minimum_order} SEK excluding VAT per consignment.",
            "Orders below {minimum_order} SEK before VAT may be refused or carry a handling fee.",
        ),
    ),
    (
        "payment",
        "Payment terms",
        (
            (
                "Payment is due 30 days after receipt of a correct bill. Disputed items may be withheld "
                "until resolved."
            ),
            (
                "{buyer} pays correct bills within 30 days. Amounts under dispute are paid only after "
                "the dispute is settled."
            ),
        ),
    ),
)

# Which clause a reviewer should read for each anomaly type (label for retrieval hit@3).
CLAUSE_FOR_ANOMALY: dict[str, str | None] = {
    "price_above_agreement": "price_lock",
    "unit_confusion": "units",
    "quantity_mismatch": "delivery",
    "duplicate": "invoicing",
    "wrong_vat": None,  # VAT comes from the dated VAT table, not from the contract
}

DELIVERY_DAYS = {
    "S01": "Tuesdays and Fridays",
    "S02": "Mondays, Wednesdays and Fridays",
    "S03": "Mondays and Thursdays",
    "S04": "Wednesdays",
    "S05": "Tuesdays and Thursdays",
    "S06": "Fridays",
}

# Same days as numbers (Monday = 0), used to pick delivery dates that match the contract.
DELIVERY_WEEKDAYS = {
    "S01": (1, 4),
    "S02": (0, 2, 4),
    "S03": (0, 3),
    "S04": (2,),
    "S05": (1, 3),
    "S06": (4,),
}

MINIMUM_ORDER = {"S01": 1500, "S02": 800, "S03": 1000, "S04": 2000, "S05": 2500, "S06": 3000}
