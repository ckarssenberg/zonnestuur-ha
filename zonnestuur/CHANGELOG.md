# Wijzigingen

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
