"""Tarieven per energieleverancier, zodat de besparing klopt met je eigen contract.

Bedragen in euro per kWh. Leveranciers passen dit een paar keer per jaar aan: de app laat altijd zien
wanneer het is gecontroleerd en de gebruiker kan elk bedrag zelf overschrijven.

Dynamisch:
  markup      = inkoopvergoeding bovenop de marktprijs (incl. btw), per kWh afname
  feed_in     = wat de leverancier bovenop (+) of van (−) de kale marktprijs doet bij teruglevering,
                zonder salderen (dus vanaf 2027)
Vast/variabel (vanaf 2027):
  feed_in_2027   = bruto terugleververgoeding per kWh (zonder btw; vanaf 2027 geen btw op teruglevering)
  return_cost_2027 = terugleverkosten per kWh (incl. btw)

'verified': True als het bedrag op de site van de leverancier zelf staat; anders komt het van
vergelijkingssites en is het een indicatie.
"""
from __future__ import annotations

CHECKED_ON = "2026-09-27"

# Energiebelasting elektriciteit, eerste schijf, incl. 21% btw. 2027 is het voorstel uit het Belastingplan 2027.
ENERGY_TAX = {2026: 0.1108, 2027: 0.10648}


def energy_tax_for(year: int) -> float:
    if year in ENERGY_TAX:
        return ENERGY_TAX[year]
    return ENERGY_TAX[max(y for y in ENERGY_TAX if y <= year)] if year > min(ENERGY_TAX) else ENERGY_TAX[min(ENERGY_TAX)]


DYNAMIC = {
    "tibber": {"name": "Tibber", "markup": 0.0180, "feed_in": -0.0180, "monthly": 6.99, "interval": "kwartier",
               "verified": True, "source": "https://tibber.com/nl/energiecontract"},
    "frank": {"name": "Frank Energie", "markup": 0.0182, "feed_in": 0.0182, "monthly": None, "interval": "uur of kwartier",
              "verified": True, "source": "https://www.frankenergie.nl/nl/kennisbank/zonnepanelen/terugleververgoeding"},
    "anwb": {"name": "ANWB Energie", "markup": 0.0180, "feed_in": 0.0, "monthly": 8.50, "interval": "uur",
             "verified": True, "source": "https://www.anwb.nl/energie/actuele-tarieven"},
    "zonneplan": {"name": "Zonneplan", "markup": 0.0200, "feed_in": 0.0200, "monthly": 6.25, "interval": "kwartier",
                  "verified": False, "note": "plus 10% zonnebonus overdag bij positieve prijzen",
                  "source": "https://www.zonneplan.nl/salderingsregeling/terugleververgoeding-vanaf-2027"},
    "easyenergy": {"name": "easyEnergy", "markup": 0.02178, "feed_in": 0.0, "monthly": 7.00, "interval": "kwartier",
                   "verified": True, "source": "https://www.easyenergy.com/dynamische-energie/prijzen"},
    "nextenergy": {"name": "NextEnergy", "markup": 0.0210, "feed_in": 0.0, "monthly": 5.99, "interval": "uur",
                   "verified": True, "note": "plus 50% zonnebonus op de marktprijs tussen 06:00 en 22:00",
                   "source": "https://www.nextenergy.nl/dynamische-energie"},
    "vattenfall": {"name": "Vattenfall FlexPrijs", "markup": 0.0255, "feed_in": 0.0, "monthly": 7.95, "interval": "uur",
                   "verified": False, "source": "https://www.keuze.nl/energie/energieleveranciers/vattenfall/dynamisch-contract"},
    "eneco": {"name": "Eneco dynamisch", "markup": 0.0241, "feed_in": -0.0241, "monthly": 7.00, "interval": "uur",
              "verified": False, "source": "https://jeroen.nl/dynamische-energie/aanbieders/eneco"},
    "essent": {"name": "Essent dynamisch", "markup": 0.0253, "feed_in": -0.0253, "monthly": 7.49, "interval": "uur",
               "verified": False, "source": "https://www.keuze.nl/energie/energieleveranciers/essent/dynamisch-contract"},
    "greenchoice": {"name": "Greenchoice dynamisch", "markup": 0.0224, "feed_in": -0.0224, "monthly": 7.50, "interval": "uur",
                    "verified": False, "source": "https://jeroen.nl/dynamische-energie/aanbieders"},
    "budget": {"name": "Budget Energie", "markup": 0.0168, "feed_in": 0.0, "monthly": 5.99, "interval": "uur",
               "verified": False, "source": "https://www.budgetthuis.nl/energie/dynamisch-energiecontract"},
    "energiedirect": {"name": "Energiedirect dynamisch", "markup": 0.0205, "feed_in": -0.0205, "monthly": 6.99, "interval": "uur",
                      "verified": False, "source": "https://www.energiedirect.nl/energie/energiecontract/dynamisch"},
    "coolblue": {"name": "Coolblue Energie dynamisch", "markup": 0.0227, "feed_in": 0.0, "monthly": 6.20, "interval": "kwartier",
                 "verified": False, "source": "https://www.coolblue.nl/energiecontracten/dynamische-energie"},
    "vandebron": {"name": "Vandebron dynamisch", "markup": 0.0219, "feed_in": -0.0136, "monthly": 6.25, "interval": "uur",
                  "verified": False, "source": "https://vandebron.nl/blog/salderen-dynamisch-contract"},
    "engie": {"name": "ENGIE dynamisch", "markup": 0.0190, "feed_in": -0.0190, "monthly": 6.95, "interval": "uur",
              "verified": False, "source": "https://jeroen.nl/dynamische-energie/aanbieders"},
    "pure": {"name": "Pure Energie dynamisch", "markup": 0.0199, "feed_in": -0.0145, "monthly": None, "interval": "uur",
             "verified": False, "source": "https://pure-energie.nl/kennisbank/inkoop-en-verkoopvergoeding-dynamisch-contract/"},
    "innova": {"name": "Innova Energie dynamisch", "markup": 0.0149, "feed_in": -0.0050, "monthly": 6.96, "interval": "uur",
               "verified": False, "source": "https://jeroen.nl/dynamische-energie/aanbieders"},
    "vanons": {"name": "Energie VanOns dynamisch", "markup": 0.0290, "feed_in": -0.0290, "monthly": 6.99, "interval": "uur",
               "verified": False, "source": "https://jeroen.nl/dynamische-energie/aanbieders"},
}

# Vaste en variabele contracten, tarieven 2027 (nieuw 1-jarig contract). Afnameprijs verschilt per contract:
# die vult de gebruiker zelf in.
FIXED = {
    "eneco": {"name": "Eneco", "feed_in_2027": 0.0835, "return_cost_2027": 0.0422},
    "vattenfall": {"name": "Vattenfall", "feed_in_2027": 0.0828, "return_cost_2027": 0.0484},
    "essent": {"name": "Essent", "feed_in_2027": 0.0910, "return_cost_2027": 0.0884},
    "greenchoice": {"name": "Greenchoice", "feed_in_2027": 0.0815, "return_cost_2027": 0.0790},
    "budget": {"name": "Budget Energie", "feed_in_2027": 0.0868, "return_cost_2027": 0.0618},
    "energiedirect": {"name": "Energiedirect", "feed_in_2027": 0.0799, "return_cost_2027": 0.0774},
    "vandebron": {"name": "Vandebron", "feed_in_2027": 0.0643, "return_cost_2027": 0.0618},
    "engie": {"name": "ENGIE", "feed_in_2027": 0.1398, "return_cost_2027": 0.1309},
    "united": {"name": "United Consumers", "feed_in_2027": 0.1589, "return_cost_2027": 0.1539},
    "innova": {"name": "Innova Energie", "feed_in_2027": 0.0756, "return_cost_2027": 0.0729},
}
FIXED_SOURCE = "https://zonnesaldo.nl/terugleverkosten-2027"


def catalog() -> dict:
    """Voor de app: alles wat nodig is om een leverancier te kiezen."""
    return {"checked_on": CHECKED_ON, "energy_tax": ENERGY_TAX, "dynamic": DYNAMIC, "fixed": FIXED,
            "fixed_source": FIXED_SOURCE}
