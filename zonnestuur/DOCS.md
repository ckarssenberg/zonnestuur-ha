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

## Auto laden

- Staat je laadpaal in Home Assistant, dan verschijnt op het dashboard de kaart **Auto laden**. Is de laadpaal niet
  te sturen, dan stuurt Zonnestuur het laden via de auto zelf (laadschakelaar, laadstroom of start/stop-knoppen,
  zoals bij Tesla, Volkswagen, BMW, Kia/Hyundai en Renault). Kan de auto een laadlimiet instellen, dan zet
  Zonnestuur die op jouw doel.
- Vink op die kaart aan welke auto's daar laden, kies hoe laat hij vol moet zijn en tik op **Laden instellen**.
- Auto's met een accupercentage in Home Assistant (bijvoorbeeld via Tibber of de app van de fabrikant) laden precies
  tot het percentage dat je kiest. **Altijd minstens** laadt direct bij als de accu leger is dan dat.
- Hangen er meer auto's aan één laadpaal, dan herkent Zonnestuur welke eraan hangt aan de stekker-sensor van de auto.
  Lukt dat niet, tik dan de auto aan op de kaart van de laadpaal.
- Met **Nu vol laden** laadt hij meteen tot je doel. Met een **maximumprijs** laadt hij nooit duurder dan dat,
  ook niet voor de vertrektijd. Alleen onder je minimum laadt hij altijd.

## Waarom deed Zonnestuur dat?

- Het **Logboek** op het dashboard toont bij elke schakeling waarom (zon over, goedkoop uur, klaar-tijd, jij) en,
  als het apparaat weer uit is, wat het gebruikte, hoeveel uit eigen zon en wat dat opleverde.
- Bovenaan zie je of alles live is en of het schakelen vandaag gelukt is. Lukt een schakeling niet, dan probeert
  Zonnestuur het één keer opnieuw en meldt het daarna.
- **Zo rekenen we** (bij de opbrengst) legt elk bedrag uit. Vul onder Instellingen → Energiecontract ook je vaste
  kosten in, dan klopt de geschatte rekening met je factuur.

## Meldingen, weekrapport en doel

- **Witgoed:** zet je de was klaar met 'start op afstand', dan meldt Zonnestuur meteen hoe laat hij start en wat de
  stroom dan kost, en later dat hij gestart is. Vergeten een programma te kiezen? Dan krijg je na 10 minuten een seintje.

- Meldingen kunnen via de Home Assistant-app, de gratis app **ntfy**, Telegram of e-mail. Je krijgt ze alleen bij een
  storing, hoogstens één keer per dag bij een kans om geld te besparen, en op zondag 19:00 het **weekrapport**.
- Kies onder Instellingen → Jouw doel wat je belangrijk vindt. Het doel is "zoveel beter dan zonder Zonnestuur",
  zodat het seizoen niet meetelt.
- **Meetdagen** (Instellingen): op willekeurige dagen stuurt Zonnestuur een apparaat een dag niet, zodat je eerlijk
  ziet wat sturing oplevert. De meetgegevens kun je als CSV downloaden.

## In Home Assistant

Zonnestuur zet drie sensoren in Home Assistant: `sensor.zonnestuur_moment` (groen/oranje/rood: is het nu een
goed moment voor de was?), `sensor.zonnestuur_zelf_gebruikt` en `sensor.zonnestuur_besparing_maand`. Koppel het
moment aan een lamp of zet het op een dashboard. Voor een tablet aan de muur is er het **wandscherm** (link onderaan
het dashboard).

## Thuisbatterij

Onder **Instellingen > Thuisbatterij** kies je de batterij die Zonnestuur in Home Assistant vond. Hij herkent onder
meer HomeWizard, Marstek (ook via Modbus, met RS485), Zendure, Anker, EcoFlow, Sessy, Victron, Huawei, SolarEdge,
GoodWe, Growatt, Sungrow, Fox ESS, Deye, Sigenergy en Tesla, en vult de standaardwaarden van dat merk in. Lukt
herkennen niet, kies dan onder Geavanceerd **Eigen scripts**: dan werkt elk merk dat Home Assistant kan sturen.

Hoe Zonnestuur beslist:

- Elk kwartier rekent hij voor de komende uren (tot 48) uit wat het goedkoopst is: **zelf gebruiken** (de batterij
  houdt de meter op nul), **vasthouden** voor een duurder uur, of **laden van het net** op 25, 50, 75 of 100%
  vermogen. Verlies bij laden en ontladen, slijtage en schakelen tellen mee.
- Het laadpercentage volgt uit het plan: zoveel als de dure uren nodig hebben, en van het net nooit boven 95%.
  Levert slim plannen minder dan € 0,03 op, dan blijft hij gewoon zelf gebruiken.
- Vasthouden alleen als er geen zon over is; gaat er toch zon naar het net, dan laat hij los.
- Optioneel: een **winterreserve** (bijvoorbeeld 2 kWh vanaf 12 uur, te gebruiken vanaf 17 uur) en een
  **piekgrens** voor een capaciteitstarief.
- Stopt Zonnestuur, of valt Pro weg, dan gaat de batterij terug naar zijn eigen regeling.
- Is de batterij 14 dagen niet vol geweest, dan krijg je een seintje: een keer vol laden houdt het percentage juist.

De batterij staat als kaart tussen je apparaten, met het plan voor de komende 24 uur.

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
