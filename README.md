# Zonnestuur voor Home Assistant

Laat je boiler, warmtepomp, auto en wasmachine draaien op je eigen zonnestroom, of in de goedkoopste uren.

[![Bekijk de uitleg in één minuut](https://zonnestuur.nl/video/uitleg.jpg)](https://zonnestuur.nl/hoe-het-werkt.html)

[Hoe het werkt en wat je nodig hebt](https://zonnestuur.nl/hoe-het-werkt.html) · [zonnestuur.nl](https://zonnestuur.nl)

## Wat heb je nodig

- **Home Assistant OS** (of Supervised): add-ons werken niet in Home Assistant Container of Core.
- **Je slimme meter uitgelezen**: een HomeWizard P1-meter (± € 30; zet in de HomeWizard-app *Lokale API* aan),
  YouLess, Shelly EM, of een meter die al in Home Assistant zit (bijv. via een P1-kabel en DSMR).
  Zonder zonnepanelen is geen meter nodig.
- **Iets om te sturen**: alles wat al in Home Assistant zit, een slimme schakelaar voor de boiler (bijv. Shelly 1PM,
  ± € 25 tot € 45, laat die door een installateur plaatsen), een laadpaal die je op afstand kunt sturen, of
  witgoed met start op afstand (Home Connect, Miele, …).
- **Zonder zonnepanelen**: een dynamisch energiecontract.

Twijfel je? Tik aan wat je hebt op [zonnestuur.nl/hoe-het-werkt](https://zonnestuur.nl/hoe-het-werkt.html#nodig),
of in Zonnestuur bij de kennismaking.

## Installeren

1. In Home Assistant: **Instellingen > Add-ons > Add-on-winkel > ⋮ > Repositories**.
2. Voeg toe: `https://github.com/zonnestuur/zonnestuur-ha`
3. Installeer **Zonnestuur**, zet **In zijbalk weergeven** aan en start hem.

## Proberen en Pro

De eerste 30 dagen werkt alles. Daarna gaat Zonnestuur verder als **Basis** (gratis), of als **Pro** (€ 49 per jaar)
met alle apparaten, dagplanning op zon en uurprijzen, thuisbatterij en meldingen. Zie [PRO.md](PRO.md).

Copyright © 2026 Zonnestuur. Alle rechten voorbehouden.

## Licentie

Zonnestuur is geen open source. Je mag het gebruiken in je eigen huis; kopiëren, aanpassen, doorverkopen of namaken is niet toegestaan. Zie [LICENSE](LICENSE). Alle rechten voorbehouden.
