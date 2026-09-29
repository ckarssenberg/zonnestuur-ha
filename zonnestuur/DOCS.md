# Zonnestuur

Zonnestuur laat je boiler, warmtepomp, laadpaal of auto draaien op je eigen zonnestroom,
in plaats van die stroom voor bijna niets terug te leveren.

## Starten

1. Installeer en start de add-on. Zet **In zijbalk weergeven** aan.
2. Klik in de zijbalk op **Zonnestuur**. De koppel-assistent start.
3. Kies een meter: een HomeWizard P1, Shelly EM, of een vermogenssensor uit Home Assistant.
4. Kies wat Zonnestuur mag sturen. Alles uit Home Assistant staat er al bij: je hoeft geen token aan te maken.
5. Test elk apparaat en sla op.

## Minder terugleveren

- De kaart **Teruglevering** op het dashboard toont hoeveel en wanneer je teruglevert, wat dat kost en wat helpt.
  Zonnestuur haalt daarvoor bij de eerste start het afgelopen jaar op uit de statistieken van Home Assistant.
- Onder **Instellingen > Minder terugleveren** kun je je omvormer laten begrenzen als terugleveren geld kost,
  en meldingen op je telefoon aanzetten.

## Thuisbatterij

Onder **Instellingen > Thuisbatterij** kies je de batterij die Zonnestuur in Home Assistant vond (bijvoorbeeld de
HomeWizard Plug-In Battery) en vul je capaciteit en vermogen in. Zonnestuur plant daarna elk uur of de batterij
zelf gebruikt, spaart of goedkoop laadt. De planning staat op het dashboard onder de prijsgrafiek.

## Andere systemen

- **MQTT:** heb je de Mosquitto-add-on, dan gebruikt Zonnestuur die automatisch. Zigbee2MQTT-, Tasmota- en
  Shelly-apparaten voeg je toe onder **Instellingen → Apparaat toevoegen via MQTT…**
- **Homey Pro:** vul het adres en een API-sleutel in onder **Instellingen → Andere systemen**.
- **Laadpalen via OCPP:** zet de OCPP-server aan en stel in de laadpaal de server in op
  `ws://<adres van Home Assistant>:8887/<naam>` (OCPP 1.6J).

## Veiligheid

- De app is alleen te openen via Home Assistant. Wil je hem ook los op je netwerk openen (de poort staat bovenaan in het logboek),
  stel dan in Zonnestuur onder **Instellingen > Beveiliging** een wachtwoord in.
- Zonnestuur zet alleen aan, uit, hoger of lager wat jij kiest. Handmatig aan of uit gaat altijd voor.

## Instellingen en gegevens

Alles staat in de map van de add-on (`/data`) en blijft bewaard bij updates en in back-ups van Home Assistant.
