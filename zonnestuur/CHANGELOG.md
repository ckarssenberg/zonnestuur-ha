# Wijzigingen

## 0.24.1 – Melding als de was klaarstaat
- Zet je de was- of droogmachine op 'start op afstand', dan krijg je meteen een melding hoe laat Zonnestuur hem start en wat de stroom dan kost, en een melding als hij gestart is.
- Staat start op afstand aan maar is er geen programma gekozen, dan zegt Zonnestuur dat (op de kaart en na 10 minuten met een melding).
- Aan of uit bij Instellingen → Meldingen ("Witgoed").

## 0.24.0 – Slimmer batterijladen voor elk merk, laden via elke auto
- Batterij: elk kwartier een nieuw plan over de bekende prijzen (tot 48 uur), met vasthouden, laden op vermogensstappen, verlies per richting, slijtage en schakelkosten. Laadt tot wat de dure uren nodig hebben (van het net hoogstens 95%) en doet niets als het minder dan € 0,03 oplevert.
- Batterij: laadsessie stopt bij het doel en houdt dan vast; vasthouden stopt als er zon naar het net gaat.
- Batterij: winterreserve, piekgrens (capaciteitstarief), werkdag en weekend apart geleerd, onmogelijke uitlezingen genegeerd, seintje als hij 14 dagen niet vol was.
- Batterij: veilig terug naar zijn eigen regeling bij stoppen, zonder Pro of bij een andere instelling.
- Meer batterijmerken herkend (o.a. Marstek via Modbus met RS485 en 'force mode', Huawei, SolarEdge, GoodWe, Growatt, Sungrow, Victron, Tesla), met standaardwaarden per merk. Eigen scripts voor elk ander merk.
- Auto: ruim 45 modellen, en laden via de auto zelf als de laadpaal niet te sturen is (schakelaar, laadstroom of start/stop-knoppen). De laadlimiet in de auto volgt je doel.
- Dashboard logischer: Nu, Planning (vooruit), Apparaten (bedienen, met de batterij als kaart en het logboek) en Inzicht (terugkijken). Nieuwe batterijkaart met het plan voor 24 uur.
- Wandscherm toont ook de thuisbatterij.

## 0.23.0 – Auto's koppelen en laden tot een percentage
- Auto's uit Home Assistant (bijvoorbeeld via Tibber, SEAT/Volkswagen, Stellantis, Tesla, Kia/Hyundai) koppelen aan de laadpaal, met hun accupercentage.
- Laden tot een percentage in plaats van een schatting per kilometer; tussen twee metingen schat Zonnestuur het percentage zelf bij.
- Per auto: laden tot (doel), altijd minstens (direct laden als de accu leger is), model, accu en lader in de auto.
- Meerdere auto's aan één laadpaal: Zonnestuur herkent welke eraan hangt, of je tikt hem aan.
- Nu vol laden met één tik, en een maximumprijs per kWh.
- Plan en kosten van het laden, vergeleken met meteen laden. Past het niet meer vóór de vertrektijd, dan zegt Zonnestuur hoeveel procent het wél wordt.
- Laadvermogen per auto: een auto die op één of twee fasen laadt krijgt de juiste stroom.
- Waarschuwing als het percentage van een auto oud is of de auto geen verbinding heeft.
- Herkennen van apparaten werkte niet als er ook een thuisbatterij gevonden werd. Opgelost.

## 0.22.0 – Auto laden en nieuw wandscherm
- Auto laden in twee tikken: Zonnestuur vindt je laadpaal en auto in Home Assistant, herkent 1 of 3 fasen en vraagt alleen hoe laat hij vol moet zijn en hoeveel je rijdt.
- Vertrektijd en klaar-tijd met − en + direct op de apparaatkaart.
- Nieuw wandscherm: de stroomklok met de prijs (of zon) van de komende 24 uur, het advies van nu, bediening per apparaat en instellingen per scherm (dag/nacht, dimmen, welke apparaten).
- Zonder zonnepanelen geeft het 'goed moment' de goedkope uren aan.
- Waarschuwing als 'zonnepanelen' aan staat terwijl je nooit teruglevert.
- Verbruik van een warmtepomp meten via zijn aan/uit-sensor als er geen vermogensmeting is.

## 0.21.0 – Laten zien wat het doet en oplevert
- Logboek: bij elke schakeling waarom, en achteraf wat het gebruikte, hoeveel uit eigen zon en wat het opleverde. Ook op elke apparaatkaart.
- Betrouwbaarheid: controle of schakelen echt lukte (met één nieuwe poging), live/lokaal-status en een melding als het misgaat.
- Tarieven 2027: vaste kosten, netbeheer en belastingvermindering, voor een geschatte rekening die klopt met je factuur. Terugleverkosten 2027 bijgewerkt.
- Eerlijkere besparing: bij een dynamisch contract vergeleken met de gemiddelde prijs van de dag. Uitleg onder "Zo rekenen we".
- Meldingen ook zonder Home Assistant (ntfy, Telegram, e-mail), met vaste regels: storingen direct, kansen hoogstens één per dag, rust 's nachts.
- Weekrapport op zondag, ook als pagina.
- Eigen doel in de Zonnecoach, gerekend als "beter dan zonder Zonnestuur", en een korte kennismaking.
- Ingrijpen met één tik: nu aan of vandaag overslaan, zichtbaar in de planning. Per apparaat "nooit vóór" een tijd.
- Meetdagen zonder sturing, met export van de meetgegevens.
- Traploze vermogensregelaar via Home Assistant (boiler volgt precies het overschot).
- Wandscherm met stoplicht "goed moment nu", en sensoren in Home Assistant.
- Zonnecoach: eerst tips om iets te koppelen, de thuisbatterij pas als de goedkopere stappen gezet zijn.
- Contractcheck 2027: welk contract met jouw verbruik het goedkoopst is.
- Plan naast werkelijk in de grafiek van vandaag.

## 0.20.1
- Welkomstscherm van de koppel-assistent noemt ook de P1-kabel van de Zonnestuur Box.

## 0.20.0 – Zonnestuur Box
- Kant-en-klaar SD-kaartbeeld voor de Zonnestuur Box, gebouwd door GitHub Actions.
- Wifi instellen zonder kabel: de Box maakt zelf de wifi "Zonnestuur-instellen" met een instelpagina.
- Automatische updates op de Box, met controle en terugval naar de vorige versie.
- Warmwater-vangnet: een script op de Shelly verwarmt de boiler zelf als de Box een paar uur stil is (ook voor de add-on).

## 0.19.0 – P1-kabel
- Slimme meter rechtstreeks uitlezen via een P1-kabel (USB), zonder HomeWizard of andere dongel. De koppel-assistent vindt de kabel zelf.

## 0.18.0 – Overzichtelijker dashboard
- Indeling in Nu, Planning, Apparaten en Inzicht, met een vaste navigatiebalk bovenaan.
- Nieuwe kaart "Wat Zonnestuur je oplevert": deze maand, vandaag, dit jaar en sinds de start, inclusief de thuisbatterij, met de opbrengst per dag.
- Dubbele adviezen samengevoegd in de Zonnecoach; op de telefoon staat de opbrengst direct onder 'Nu'.

## 0.17.0 – Koppelen met (bijna) alles
- MQTT: Zigbee2MQTT-stekkers, Tasmota, Shelly en eigen topics; als add-on automatisch de Mosquitto-broker van Home Assistant.
- Homey Pro: meter, schakelaars en thermostaten.
- Meters: YouLess, DSMR-reader, ESPHome (SlimmeLezer), MQTT en Homey.
- SG-ready warmtepompen via twee relais.
- Laadpalen via OCPP 1.6J (Alfen, Peblar, Wallbox en andere): traploos laden zonder Home Assistant.

## 0.16.0 – Zonnecoach
- Nieuw kerncijfer: hoeveel van je zonnestroom je zelf gebruikt (vandaag en deze week), plus hoeveel van je verbruik uit eigen zon komt.
- Zonnecoach op het dashboard: je score met de laatste 14 dagen, het zonnevenster van vandaag of morgen, en persoonlijke tips met stappen en wat ze per jaar opleveren.
- Zonder zonnepanelen: welk deel van je stroom je in goedkope uren gebruikt, met tips.
- Ochtendmelding op zonnige dagen: "Tussen 11:00 en 15:00 ± 9 kWh over" (met meldingen aan).

## 0.15.0 – Zelflerend
- Zonnestuur leert je huis kennen: eigen verbruik per uur (werkdag en weekend apart, zonder de gestuurde apparaten en de batterij), sluipverbruik en piekmomenten.
- Leert per apparaat hoe lang het echt nodig heeft en plant daarmee (uit te zetten per apparaat).
- Met een opwek-sensor stelt Zonnestuur de zonvoorspelling bij voor jouw dak.
- Zon-, prijs- en batterijplanning rekenen met het geleerde huis in plaats van vaste aannames.
- Nieuwe kaart "Wat Zonnestuur over je huis weet".
- Alles per apparaat in te stellen: meerdere klaar-tijden, weekdagen, garantie, looptijd, drempels en tijden.
- Mooier op grote schermen.

## 0.14.1
- Dashboard terug naar het vertrouwde ontwerp van 0.13.
- Klaar-tijden kunnen nu per weekdag gelden (bijvoorbeeld alleen op werkdagen).

## 0.13.0 – Zonnestuur Pro
- 30 dagen alles proberen; daarna Zonnestuur Basis (gratis) of Pro (€ 49 per jaar). Zie PRO.md.
- Licentiesleutel activeren onder Instellingen → Zonnestuur Pro. Controle gebeurt lokaal, zonder account of internet.

## 0.12.0 – Thuisbatterij en nieuw dashboard
- **Thuisbatterij sturen**: HomeWizard Plug-In Battery, Zendure, Marstek, Sessy, Victron en andere batterijen in Home Assistant. Zonnestuur plant elk uur wanneer de batterij zelf gebruikt, spaart voor de dure uren of goedkoop laadt, op basis van je eigen verbruik, de zonvoorspelling en de uurprijzen. Zon-overschot gaat eerst naar je apparaten.
- **Nieuw dashboard**: in één zin wat er nu gebeurt, live energiestroom met batterij, en een dagplanning per apparaat en batterij onder de prijsgrafiek.
- Zonder zonnepanelen kloppen alle teksten; lege grafieken tonen uitleg; storingen in gewone taal en alleen zolang ze spelen.

## 0.11.1
- Teruglevering-kaart toont zonder teruglevering je afname per uur en gemiddelde prijs.

## 0.11.0 – Minder terugleveren
- Nieuwe dashboardkaart **Teruglevering**: hoeveel en op welke uren je teruglevert, wat dat per jaar kost, persoonlijk advies en een batterijsimulatie op je eigen uurdata.
- Haalt bij de eerste start het afgelopen jaar op uit de statistieken van Home Assistant.
- Alle apparaten plannen samen: niet meer twee apparaten op hetzelfde zonne-overschot.
- Omvormer begrenzen als terugleveren geld kost (negatieve prijs); weggeknepen zon gaat eerst naar je apparaten.
- Meldingen op je telefoon: veel teruglevering terwijl alles al draait, negatieve prijzen morgen, apparaat reageert niet.

## 0.10.0
- Prijsgrafiek per uur (dynamisch) of je vaste tarieven, en je verbruik per dag, week, maand en jaar.
