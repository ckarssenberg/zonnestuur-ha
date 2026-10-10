# Zonnestuur

Zonnestuur laat je boiler, warmtepomp, laadpaal of auto draaien op je eigen zonnestroom,
in plaats van die stroom voor bijna niets terug te leveren. Zonder zonnepanelen draait alles in de goedkoopste uren.

[![Bekijk de uitleg in één minuut](https://zonnestuur.nl/video/uitleg.jpg)](https://zonnestuur.nl/hoe-het-werkt.html)

Nieuw? Bekijk de [uitleg in één minuut](https://zonnestuur.nl/hoe-het-werkt.html) en kijk
[wat je nodig hebt](https://zonnestuur.nl/hoe-het-werkt.html#nodig). In Zonnestuur zelf staat dezelfde kennismaking
onder **Uitleg** (het vraagteken bovenin).

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

1. Installeer en start de add-on. Zet **In zijbalk weergeven** aan.
2. Klik in de zijbalk op **Zonnestuur**. Eerst krijg je een korte kennismaking, daarna start de koppel-assistent.
3. Kies een meter: een HomeWizard P1, Shelly EM, of een vermogenssensor uit Home Assistant.
4. Kies wat Zonnestuur mag sturen. Alles uit Home Assistant staat er al bij: je hoeft geen token aan te maken.
5. Test elk apparaat en sla op.

## Een merk koppelen

Open in Zonnestuur **Instellingen > Een merk koppelen**. Zoek je merk (ruim 400 in de lijst) en druk op **Koppelen**.
Zonnestuur start de Home Assistant-integratie zelf en laat de stappen in zijn eigen scherm zien, ook inloggen bij de
fabrikant. Integraties die niet standaard in Home Assistant zitten (HACS), installeert Zonnestuur vanaf GitHub; Home
Assistant herstart daarna één keer. Per merk staat erbij wat je extra nodig hebt (een dongle, een module, of een
instelling die de installateur moet aanzetten).

Matter-apparaten koppel je met de Matter-code; daarvoor heb je de Matter Server-app in Home Assistant nodig. Zigbee
gaat via ZHA met een Zigbee-dongle.

## Minder terugleveren

- De kaart **Teruglevering** op het dashboard toont hoeveel en wanneer je teruglevert, wat dat kost en wat helpt.
  Zonnestuur haalt daarvoor bij de eerste start het afgelopen jaar op uit de statistieken van Home Assistant.
- Onder **Instellingen > Omvormer** koppel je je omvormer. Dan weet Zonnestuur precies wat je panelen opwekken, en
  knijpt hij de omvormer af als terugleveren geld kost. Daarna gaat hij vanzelf terug naar vol vermogen.
- Dat kan via Home Assistant, of **rechtstreeks** (ook op de Zonnestuur Box): Fronius, SMA, SolarEdge, Huawei,
  GoodWe (hybride), Sungrow, Kostal, Hoymiles via OpenDTU of AhoyDTU, APsystems EZ1, Victron en elk merk met
  SunSpec Modbus. Enphase en SolaX kan hij rechtstreeks alleen uitlezen.
- Had de installateur al een eigen exportgrens ingesteld, dan zet Zonnestuur die na het begrenzen precies zo terug.
  Valt Zonnestuur weg terwijl hij begrenst, dan laat hij de omvormer bij de volgende start meteen los.

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

## Weer en tips

- Is vandaag duidelijk zonniger dan morgen en overmorgen, dan krijg je 's ochtends een tip: zet vandaag de was aan,
  laat de auto vandaag laden. Zonder panelen krijg je een tip als vandaag goedkoper is dan morgen.
- **Instellingen > Jouw doel > Sturing**: *zelfconsumptie* (zo veel mogelijk eigen zon), *prijs* (de auto mag ook
  laden als 30% uit het net komt) of *netvriendelijk* (niets inplannen in de avondpiek of in dure nettarief-tijdvakken).
- Per apparaat (tik op het apparaat bij Instellingen): bij een laadpaal het minimale aandeel zon en een fasewissel;
  bij warm water en verwarming een wekelijkse legionellaronde en hoeveel graden lager tijdens dure stroom.
- **Instellingen > Contract > Terugleverkosten en nettarief**: per kWh, staffel of vast bedrag, en het nettarief per tijdvak.

## Uitleg

- Het vraagteken rechtsboven op het dashboard opent de pagina Uitleg, met per onderdeel wat je ziet en hoe Zonnestuur rekent.

## Dashboardkaarten

- Zonnestuur zet vijf kaarten klaar in Home Assistant. Ga naar je dashboard → Bewerken → Kaart toevoegen en zoek op **Zonnestuur**.
- YAML-dashboards: bron `/local/zonnestuur/zonnestuur-cards.js` (type: module), daarna bijvoorbeeld `type: custom:zonnestuur-card`.

## Telefoonwidget

- Open **Telefoonwidget** onderaan de kaart Opbrengst en kies in je browser "Zet op beginscherm". De pagina ververst elke 30 seconden.
- In de Home Assistant-app kun je ook een widget maken met de sensoren `sensor.zonnestuur_moment`, `sensor.zonnestuur_prijs_nu`,
  `sensor.zonnestuur_goedkoopste_blok`, `sensor.zonnestuur_volgende` en `sensor.zonnestuur_tip`.

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
- **Programma kiezen:** bij Miele en Home Connect kies je het programma op de kaart van de machine. Vul de machine,
  zet start op afstand aan, kies het programma: Zonnestuur start hem met dat programma op het goedkoopste moment.

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

HomeWizard Plug-In Battery, Marstek Venus, Zendure SolarFlow/Hyper en Victron (ESS) kan Zonnestuur ook
**rechtstreeks** sturen, zonder Home Assistant-integratie (handig op de Zonnestuur Box). Kies het merk onder
**Koppeling** en druk op **Test verbinding**. HomeWizard koppel je met de knop op de P1-meter.

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
