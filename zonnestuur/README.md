# Zonnestuur

**Je huis kiest zelf het beste moment.** Zonnestuur laat je boiler, warmtepomp, auto en wasmachine draaien op je
eigen zonnestroom, of in de goedkoopste uren van de dag. Jij kiest hoe laat iets klaar moet zijn; Zonnestuur kiest de
beste uren ervoor.

[![Bekijk de uitleg in één minuut](https://zonnestuur.nl/video/uitleg.jpg)](https://zonnestuur.nl/hoe-het-werkt.html)

[Bekijk de uitleg in één minuut](https://zonnestuur.nl/hoe-het-werkt.html) · [Wat heb je nodig?](https://zonnestuur.nl/hoe-het-werkt.html#nodig)

## Waarom nu

Op 1 januari 2027 stopt de salderingsregeling. Een kWh van je eigen zon die je zelf gebruikt, bespaart je dan
± 20 tot 30 cent; dezelfde kWh terugleveren levert een paar cent op, en soms betaal je er zelfs voor.
Geen zonnepanelen? Met een dynamisch contract zet Zonnestuur je apparaten aan in de goedkoopste uren.

## Zo werkt het

1. **Kijken**: via je slimme meter ziet Zonnestuur elke paar seconden of je stroom over hebt, met de zonverwachting
   en de stroomprijs per kwartier.
2. **Plannen**: elk kwartier rekent hij uit wanneer elk apparaat het beste kan draaien.
3. **Schakelen**: hij zet je apparaten aan en uit, via Home Assistant, een slimme schakelaar of je laadpaal.

## Wat kun je ermee

| | |
|---|---|
| **Warm water** | de boiler verwarmt op je zon, altijd warm op tijd |
| **Warmtepomp** | warmte opslaan als stroom goedkoop is |
| **Auto laden** | op de zon of goedkoop, en vol op de tijd die jij kiest |
| **Was en vaat** | start vanzelf op het beste moment, met een melding |
| **Thuisbatterij** | laden en ontladen op het juiste moment |
| **Zonnepanelen** | afknijpen als terugleveren geld kost |
| **Huisscherm** | op je Nest Hub: is stroom nu goedkoop? Met radio |
| **Inzicht** | wat je bespaart, tips en welk contract het beste past |

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

## Starten

1. Start de add-on en zet **In zijbalk weergeven** aan.
2. Klik in de zijbalk op **Zonnestuur**. Je krijgt eerst een korte kennismaking (2 minuten, met video).
3. Daarna zoekt Zonnestuur je meter en apparaten. Alles uit Home Assistant staat er al bij; je hoeft geen token aan te maken.

De eerste 30 dagen kun je alles gebruiken. Meer over Basis en Pro: zie de documentatie.

## Veiligheid

- Alles draait bij jou thuis; je meterdata gaan niet naar een cloud.
- Zonnestuur zet alleen aan, uit, hoger of lager wat jij kiest. Handmatig aan of uit gaat altijd voor.
- De app is alleen te openen via Home Assistant. Wil je hem ook los op je netwerk openen (de poort staat bovenaan in het logboek),
  stel dan in Zonnestuur onder **Instellingen > Beveiliging** een wachtwoord in.

## Instellingen en gegevens

Alles staat in de map van de add-on (`/data`) en blijft bewaard bij updates en in back-ups van Home Assistant.
