# Wijzigingen

## 0.49.0
- Hoofdzekering bewaken: vul bij Instellingen > Meter je aansluiting in (bijv. 3 × 25 A). Zonnestuur houdt auto, warmtepomp, boiler en batterij samen onder de grens: eerst laadt de auto zachter, daarna wacht wat het minst belangrijk is.
- Op de telefoon staat het menu nu als vaste balk onderin, zoals een app.

## 0.48.0
- Auto: Zonnestuur meet hoe snel je auto echt laadt (ook dat het boven ± 80% langzamer gaat) en rekent daar de hele tijd mee. De laadkaart laat de gemeten snelheid zien.
- Vergelijken: vul per laadbeurt in wat de app van je leverancier zegt dat hij kostte (bijv. inclusief Grid Rewards van Tibber); dan telt dat bedrag. Per leverancier staat erbij waar je het vindt.
- Geeft de koppeling van je leverancier de opbrengst door (bijv. Powerplay van Zonneplan), dan telt Zonnestuur die vanzelf mee.
- De vergelijking rekent voor Zonnestuur met de echte laadsnelheid van je auto, niet met die op papier.

## 0.47.2
- Prijsverwachting: haalt eerst de recentste dagen op en leert opnieuw zodra er meer historie is, zodat hij na de installatie sneller goed voorspelt.

## 0.47.1
- Thuisbatterij, "Meer instellingen": elke instelling in een eigen blok met uitleg in gewone woorden, schakelaars in plaats van vinkjes en een samenvatting van wat er nu ingesteld staat.
- Nieuw deel "Voor de installateur" met uitleg bij de piekgrens, de verbinding en de sturing via de koppelmotor.

## 0.47.0
- Auto: "Moet de auto later klaar zijn?" Kies een dag en tijd (bijv. maandag 08:00) en hoe vol. Zonnestuur laadt dan in de goedkoopste of zonnigste uren tot dat moment, ook over meerdere dagen.
- Prijsverwachting uit het weer: veel wind of zon maakt stroom goedkoop. Zonnestuur voorspelt de prijs voor de dagen die de beurs nog niet heeft gegeven en laat per dag zien wat hij verwacht en waarom. Auto en thuisbatterij plannen daarmee.
- Prijscontrole: met de prijssensor van je leverancier (bijv. Tibber) legt Zonnestuur elk kwartier zijn eigen prijs ernaast en corrigeert zichzelf als het structureel afwijkt.
- Vergelijken: per laadbeurt zie je hoe laat, hoeveel kWh en de prijs per kWh, zodat je het naast de app van je leverancier kunt leggen.
- Thuisbatterij instellen in vijf korte vragen; alles wat je meestal niet nodig hebt staat onder "Meer instellingen".
- Instellingen opnieuw ingedeeld in vijf groepen met een vaste navigatie, en een Opslaan-knop die altijd in beeld is.

## 0.46.3
- Huisscherm (Nest Hub): onderaan zie je nu wat er draait: auto laden (ook als Tibber of een andere app laadt), de boiler of warmtepomp en de thuisbatterij.

## 0.46.2
- Laadt Tibber of een andere app je auto, dan zie je dat nu op de echte laadkaart: de auto met oplichtende laadkabel, het accupercentage en wat Zonnestuur zou doen.
- De autofoto knippert niet meer bij het verversen van het dashboard.

## 0.46.1
- Laadkaart: naast "Koppelen" nu ook "Alleen meekijken". Laadt Tibber, Jedlix of een andere app je auto, dan blijft die laden en laat Zonnestuur live zien wat er gebeurt.

## 0.46.0
- Stuurt een leverancier je batterij of het laden van je auto? Dan zie je dat nu live op het dashboard: wat hij nu doet, hoeveel er sinds 12:00 geladen is en wat het kostte, en wat Zonnestuur nu zou doen.

## 0.45.0
- Stuurt je leverancier (Zonneplan, Frank, Tibber, …) je thuisbatterij? Kies "Wie stuurt de batterij?": je leverancier (Zonnestuur kijkt mee), Zonnestuur, of afwisselend per maand.
- Nieuwe pagina Vergelijken: per maand wat je leverancier deed, wat Zonnestuur had gedaan en het verschil, met advies. Ook voor slim laden van de auto (Tibber, Jedlix, ANWB, …).
- Sessy rechtstreeks koppelen; Zonnestuur kan zelf wisselen tussen Frank en eigen sturing.
- De batterij legt elke keuze uit, bijvoorbeeld "nu geen stroom uit de batterij: bewaren voor 18:00".

## 0.44.0
- Thuisbatterij: kies je doel (slim, zelfvoorzienend of maximaal verdienen). Met een dynamisch contract kan de batterij nu ook verkopen aan het net bij een prijspiek (Marstek, Zendure, Victron en batterijen met een vermogen-instelling).
- Auto: laden op wat je rijdt. Vul je kilometers per werkdag en weekenddag in; bij kou rekent Zonnestuur met meer verbruik.
- Nettarief vanaf 2029 (voorstel van de ACM): Zonnestuur rekent vanaf dan vanzelf met de dure en goedkope uren.

## 0.43.0
- Teksten opgeschoond: koppelen via merken gaat nu overal via "de koppelmotor". Uitleg en instellingen zijn eenvoudiger, de uitlegvideo is bijgewerkt.

## 0.42.0
- Nieuwe licentiesleutels, zodat Pro straks automatisch aangaat na aanmelden of betalen. Had je al een Pro-sleutel, dan krijg je van ons een nieuwe.

## 0.41.0
- Nieuw: koppelen met Domoticz, openHAB en ioBroker (Instellingen → Andere systemen). Meters, schakelaars en thermostaten uit die systemen kun je meteen in Zonnestuur gebruiken.
- "Wat heb je nodig?" gaat nu uit van de Zonnestuur SD-kaart voor je eigen Raspberry Pi.

## 0.40.1
- Bovenaan het dashboard stond 's avonds soms "Wacht op de zon", terwijl het volgende moment een goedkoop uur is. Nu staat er "Volgende slimme stap", met de reden en de prijs.

## 0.40.0
Grote controle van alle koppelingen, met veel verbeteringen:
- Omvormer: wordt altijd weer losgelaten, ook zonder Pro, na een storing of herstart, en als je hem verwijdert. Fronius en SolarEdge lezen hun vol vermogen zelf uit. APsystems en SunSpec krijgen precies hun eigen instelling terug.
- Thuisbatterij: krijgt bij stoppen echt zijn eigen regeling terug (Zendure stond stil, Victron verloor zijn eigen grenzen), ook na een herstart. Niet meer elke 10 seconden opnieuw losgelaten. HomeWizard stopt met volladen bij loslaten.
- Auto: zonder vermogensmeting niet meer onterecht "vol" na 10 minuten. Laden op 1 fase op het juiste vermogen. Een OCPP-laadpaal ziet nu wanneer de stekker eruit gaat. Enode krijgt niet meer steeds dezelfde opdracht.
- P1-kabel: herstelt zich na eruit en erin trekken, en controleert de foutcode van elk telegram.
- Tasmota met meerdere kanalen, YouLess LS110, ESPHome (nieuwe versies), Shelly via MQTT en Bosch/Siemens via Home Connect werken nu.
- SG-ready: de warmtepomp komt bij omschakelen niet meer even in de stand "geblokkeerd".
- Merkenlijst: koppelingen die alleen met de hand in Home Assistant kunnen, krijgen uitleg in plaats van een knop die vastloopt. Een opgeheven koppeling (Panasonic) vervangen. Duidelijkere foutmeldingen bij het koppelen.

## 0.39.1
- Grote koppen zijn beter leesbaar: de woorden staan niet meer zo dicht op elkaar.
- De uitlegvideo is vernieuwd.

## 0.39.0
- Nieuw: een korte kennismaking bij de eerste start, met een uitlegvideo van één minuut. Wat Zonnestuur doet, waarom het vanaf 2027 telt, hoe het werkt en wat je ermee kunt. Later terug te vinden via Uitleg.
- Nieuw: "Wat heb je nodig?". Tik aan wat je thuis hebt, en je ziet meteen wat Zonnestuur kan en of je nog iets nodig hebt (met richtprijzen).

## 0.38.0
- Meld je aan als proefhuis vanuit Zonnestuur (Instellingen → Zonnestuur Pro): tijdens de testfase krijg je Pro dan gratis.
- Pro kopen kan straks direct vanuit Zonnestuur met iDEAL; Pro gaat na het betalen vanzelf aan.
- Zonnestuur laat een paar keer per dag weten dat het draait (zonder persoonlijke gegevens). Zo kunnen we je proefperiode verlengen. Uit te zetten bij Hulp.

## 0.37.1
- Zonnestuur wordt voortaan geleverd als afgeschermd pakket, zonder leesbare broncode. Voor jou werkt alles hetzelfde.

## 0.37.0
- Voorbereiding op een beter beschermde versie: de Box kan voortaan updates in een nieuwe, afgeschermde vorm ontvangen. Voor jou verandert er niets.

## 0.36.1
- Het stroomschema op het scherm Nu past weer helemaal in beeld op de telefoon: de naam en het vermogen onder het net-icoon en de pauzeknop vielen half buiten het blok.

## 0.36.0
- Nieuw: knop **Feedback geven** bovenaan het scherm (het tekstwolkje). Kies of er iets misgaat, of je een idee hebt of een vraag. Bij een idee of vraag gaat alleen je tekst mee; bij een probleem ook de gegevens die nodig zijn om het op te lossen.

## 0.35.3
- De datum bovenaan het scherm Nu valt niet meer half in de ronde hoek van het blok; er zit weer netjes ruimte omheen, op telefoon en computer.
- Pro is gratis tijdens de testfase: na de 30 proefdagen krijg je via info@zonnestuur.nl een gratis Pro-sleutel.

## 0.35.2
- Zonnestuur heeft een eigen plek op GitHub: github.com/zonnestuur/zonnestuur-ha. Het oude adres stuurt vanzelf door; je hoeft niets te doen.

## 0.35.1
- Huisscherm hapert niet meer: na een korte internetstoring kon het scherm op een Nest Hub Home Assistant niet meer bereiken. Het bleef staan, maar sprong elke 30 seconden even weg. Zonnestuur ziet dat nu (drie keer kort na elkaar weg) en zet het scherm netjes opnieuw neer. Eén losse onderbreking, zoals een herstart, laat het met rust.
- Bestaande huisschermen worden na de update vanzelf bijgewerkt.

## 0.35.0
- Nieuwe huisstijl, gelijk aan zonnestuur.nl: koel daglicht-grijs, strakke witte vlakken, vette koppen met een lijn eronder, zwarte hoofdknoppen en omlijnde tweede knoppen. Geldt voor het dashboard, Instellingen, Koppelen, Huisscherm, Hulp en de uitleg, in licht en donker.

## 0.34.1
- Koppelen zonder Enode: Enode (betaald) staat niet meer in de standaardlijst. Elke auto kan gratis via de laadpaal (Zonnestuur leert hoe lang laden duurt en begint op tijd), en waar het kan via de gratis koppeling van het automerk of evcc. Nieuw: Volkswagen ID en Audi via hun eigen koppeling.
- Heb je zelf een Enode-account, dan vul je dat in onder Instellingen; het koppelscherm toont de Enode-route dan weer.

## 0.34.0
- **Nieuwe laadkaart**: eerst wat er met je auto gebeurt. Welke auto, klaar om, wanneer hij laadt, wat het kost, en één knop "Nu laden". De laadinstellingen staan ingeklapt eronder. Met een foto van een auto aan de laadpaal; bij stekker eruit vervaagt de laadpaal, tijdens het laden licht hij op.
- **Hulp en diagnose** (Instellingen → Hulp en problemen): zie in één oogopslag of meter, Home Assistant en apparaten werken, en meld een probleem met één knop. Zonnestuur stuurt een diagnose mee, zonder wachtwoorden, adressen of tokens.
- **Fouten automatisch melden**: fouten in Zonnestuur en in het scherm gaan gefilterd en ontdubbeld naar de Zonnestuur-server, zodat ze opgelost kunnen worden voordat je ze merkt. Uit te zetten op de hulppagina.
- **Testkanaal voor de Box**: proefhuizen krijgen nieuwe versies als eerste; de rest pas als de versie stabiel is.

## 0.33.2
- Apparaten: na een afgeronde wasbeurt verdwenen de wasmachine en de apparaten daarna uit het overzicht. Opgelost, en één kaart met een fout houdt de andere niet meer tegen.
- Koppellijst: Nilan (Compact P, Compact P GEO, VPL) en Genvex toegevoegd, met de lokale koppeling via de Nilan Gateway of via Modbus.

## 0.33.1
- Huisscherm: zenders die in de lijst alleen een onbeveiligd adres hebben (zoals NPO en 538) probeert Zonnestuur via https, zodat ze ook op het scherm spelen.
- Twee apparaten met dezelfde naam zijn uit elkaar te houden.

## 0.33.0
- **Huisscherm** (Instellingen → Huisscherm en radio): kies een Google Nest Hub, Chromecast of tv, vink aan wat erop staat (stroomprijs, weer, afval, witgoed, agenda, auto, besparing, nieuws) en kies een radiozender met tijden, dagen en volume. Zonnestuur maakt het dashboard, de radio-knop en de automatisering in Home Assistant zelf aan.
- Zenders zoeken in de open zenderlijst van radio-browser.info, met luisteren voordat je kiest; of vul zelf een streamadres in.
- Nest Mini, Nest Audio en Google Home: alleen radio op vaste tijden.
- Op de Box koppelt Zonnestuur Google Cast met één knop.

## 0.32.2
- In de Home Assistant-app: bovenaan doorslepen in Zonnestuur ververst niet meer per ongeluk de hele app (en vraagt dus ook niet meer om opnieuw in te loggen).

## 0.32.1
- Huisscherm (Nest Hub): grote knop **Radio aan / Radio uit** naast de klok. Uit blijft uit tot de volgende dag; aan start de zender weer (`radio_stream`, anders de laatst gespeelde).
- Dashboard: verloop van vandaag toont weer het netverbruik bij de prijsstand (zonder zonnepanelen); dat werd een tijd niet bewaard.
- Dashboard laadt zichzelf binnen 5 seconden opnieuw als iets mislukte (bijv. tijdens een herstart van Home Assistant) en ververst meteen als je terugkomt in de app. Eén kapotte grafiek houdt de rest niet meer tegen.

## 0.32.0 – Alle merken koppelen
- Nieuw scherm **Koppelen**: zoek je merk (424 merken en apparaten in Nederlandse huishoudens) en zie per route wat je nodig hebt. Met één knop start je de koppeling.
- Home Assistant-integraties koppel je nu vanuit Zonnestuur: formulieren, keuzes en inloggen bij de fabrikant verschijnen in het Zonnestuur-scherm, met de Nederlandse teksten van de integratie.
- Integraties van buiten Home Assistant (HACS) installeert Zonnestuur zelf vanaf GitHub, daarna herstart Home Assistant één keer.
- Matter-apparaten koppelen met de Matter-code, en Zigbee-apparaten laten aanmelden (ZHA).
- Zonnestuur Box: ingebouwde koppelmotor (Home Assistant en Matter-server; Thread-grensrouter en evcc wanneer nodig). Daarmee werken op de Box alle merken uit de lijst. Box: Raspberry Pi 4 of 5 met 4 GB, microSD van 16 GB. Bestaande Boxen met genoeg geheugen krijgen de motor bij deze update.
- Auto's van merken die hun koppeling hebben dichtgezet (Volkswagen-groep, Ford, BYD e.a.) via Enode, en laadpalen en auto's die evcc kent (ook EEBUS, zoals EVBox Livo en Elli).
- Thuisbatterij ook via Enode.

## 0.31.0 – Omvormers rechtstreeks
- Omvormer koppelen zonder Home Assistant (ook op de Box): Fronius, SMA, SolarEdge, Huawei, GoodWe (hybride), Sungrow, Kostal, Hoymiles via OpenDTU of AhoyDTU, APsystems EZ1, Victron en elk merk met SunSpec Modbus. Enphase en SolaX alleen uitlezen.
- Zonnestuur leest de opwek dan zelf uit de omvormer en begrenst bij een negatieve terugleverprijs. Met knop Test verbinding en per merk wat je moet aanzetten.
- Veilig loslaten: een eigen exportgrens van de installateur komt precies terug, bij stoppen laat hij de omvormer los, en na een onverwachte herstart ook. Waar onbekend is of een instelling naar het geheugen van de omvormer gaat, schrijft hij hoogstens eens per 5 minuten.
- Instellingen: velden staan op de telefoon nu netjes onder elkaar over de hele breedte.

## 0.30.10 – Zonvoorspelling hersteld
- In 0.30.9 werd de zonvoorspelling niet meer opgehaald (fout in de vraag aan Open-Meteo). Hersteld; het weer van de afgelopen maand komt nu ook binnen.

## 0.30.9 – Weer, huishoudprofiel en batterijen rechtstreeks
- Zonnestuur haalt nu ook temperatuur, bewolking, regen en wind op en bewaart die per uur. Hij leert hoeveel meer stroom jouw huis gebruikt als het kouder is en schat daarmee het verbruik van vandaag en morgen. Te zien onder Geleerd.
- Het plan voor apparaten en de thuisbatterij gebruikt die weerafhankelijke verbruiksverwachting.
- Thuisbatterij rechtstreeks, zonder Home Assistant: HomeWizard Plug-In Battery, Marstek Venus, Zendure SolarFlow/Hyper en Victron (ESS). Met koppelknop voor HomeWizard en een knop Test verbinding. Werkt ook op de Box.
- Het HomeWizard-token wordt niet naar de app gestuurd.
- Uitleg over de Nest Hub bijgewerkt: één keer casten, niet steeds opnieuw.

## 0.30.8 – Groot scherm licht genoeg voor een Nest Hub
- Op een Nest Hub (of met licht: true) staat het grote scherm in een lichte stand: geen bewegende achtergrond en geen animaties. Een te zware pagina liet de Hub na een paar minuten terugvallen op de fotolijst.
- Bij een nieuwe nieuwskop wordt alleen de nieuwsbalk vervangen, niet het hele scherm.

## 0.30.7 – Scherm blijft in beeld bij radio (proef)
- De radio in het grote scherm speelt nu via een klein video-element. Een Nest Hub zet bij alleen geluid na een paar minuten de fotolijst over het scherm; zo hopen we dat het scherm blijft staan. Uit te zetten met radio_als_video: false.
- Uitleg: een Nest Hub haalt een gecast scherm na ongeveer 10 minuten weg; laat het elke 8 minuten opnieuw sturen.

## 0.30.6 – Rustiger radio, duidelijker nieuws
- De radio in het grote scherm komt zacht op (in 6 seconden) in plaats van in één keer. Optie radio_volume (0–1) voor het niveau binnen het scherm.
- Nieuws in een eigen balk: grotere kop over maximaal twee regels, de bron als label, hoe lang geleden, en een dun balkje dat laat zien wanneer de volgende kop komt.
- Staand scherm (tablet): blokjes over twee rijen.

## 0.30.5 – Het grote scherm als huisscherm
- Wasmachine, droger en vaatwasser: "klaar om 18:30" tijdens het programma, en een oplichtend "Was is klaar" als hij klaar is (Miele en andere integraties met een status en eindtijd).
- Regen: "droog tot 15:00" of "regen tot 16:00" bij het weer, uit de verwachting per uur.
- Agenda: de eerstvolgende afspraak van vandaag.
- Auto: accu en laadstatus van gekoppelde auto's.
- Besparing van deze maand bovenin, naast Zonnestuur.
- Nieuws uit meerdere bronnen tegelijk, elk met een eigen label (bijv. GLD en NOS).
- Wat het dringendst is staat vooraan; wat niet meer past valt weg, zodat het scherm rustig blijft.

## 0.30.4 – Nieuws op het grote scherm
- Optie nieuws: koppen van een nieuwsfeed (integratie Feedreader) als rustige regel onderaan het grote scherm, wisselend elke 15 seconden. Bijvoorbeeld Omroep Gelderland: nieuws: event.nieuws, nieuws_label: GLD.

## 0.30.3 – Afval en weer op het grote scherm
- Het grote scherm toont welke afvalcontainer deze week aan de weg moet, als bakje in de kleur van de container (GFT groen, papier blauw, PMD oranje, rest grijs). De avond ervoor licht hij op: "vanavond buiten zetten". Werkt vanzelf met de integratie Afvalbeheer; of geef zelf op: afval: [{entity, naam, kleur}].
- Ook het weer van nu (temperatuur en een icoon).

## 0.30.2 – Radio in het grote scherm
- Het grote scherm kan zelf de radio afspelen (optie radio_entity: een tekst-helper met de stream). Zo blijft de muziek doorspelen terwijl Zonnestuur op je Nest Hub in beeld is. Rechtsboven staat welke zender speelt.

## 0.30.1 – Groot scherm voor je Nest Hub
- Nieuwe kaart "Zonnestuur: groot scherm": schermvullend voor een Google Nest Hub of wandtablet. Is stroom goedkoop, dan groot "tot 14:00", hoe lang nog, de prijs nu en de prijs per uur met het goedkope blok oplichtend. Anders: wanneer het volgende goedkope blok begint.
- Nieuwe sensor "Zonnestuur: stroom goedkoop" (aan/uit, met tot hoe laat en het volgende blok): handig als trigger om het scherm op je Hub te zetten of een melding te sturen.
- Uitleg: zo zet je het scherm op je Nest Hub terwijl er muziek speelt.

## 0.30.0 – Kaarten voor je dashboard en widgets voor je telefoon
- Vijf Zonnestuur-kaarten voor het Home Assistant-dashboard: nu (oordeel, besparing, tips), besparing, prijs en planning, auto laden (lijntekening met oplichtende kabel) en apparaten. Zonnestuur zet ze zelf klaar: Bewerken → Kaart toevoegen → zoek op "Zonnestuur". Ze volgen het licht of donker thema en werken op een wandtablet.
- Nieuwe sensoren voor widgets in de Home Assistant-app (iPhone en Android): Zonnestuur nu, en per auto de accu met laadstatus.
- Uitleg: hoe je de kaarten en telefoonwidgets toevoegt.
- De add-on krijgt schrijfrecht op de map van Home Assistant, alleen om de kaarten in /config/www/zonnestuur te zetten.

## 0.29.8 – Laadkaart als lijntekening
- De auto op de laadkaart is nu een strakke lijntekening: één dunne lijn, met de laadpaal en een grondlijn. Zit de stekker erin, dan kleurt de kabel geel en lopen tijdens het laden de stippen naar de auto. De accu is een dunne lijn langs de dorpel; tijdens het laden licht hij op. Zit de stekker eruit, dan ligt hij naast de laadpaal.

## 0.29.6 – Eerlijk oordeel
- "Goed moment" alleen als er vandaag niet duidelijk iets goedkopers meer komt. Komt er binnenkort ≥ 3 ct per kWh goedkopere stroom, dan staat er "Kan, beter om …".

## 0.29.5 – Strak op de telefoon
- Het hele dashboard opnieuw ingedeeld voor de telefoon: compacte kop, besparing en tips bovenaan, planning op één kolom, geen zijwaarts schuiven meer (ook bij Instellingen).
- Op de telefoon start het dashboard eenvoudig (alleen wat nu telt); "Uitgebreid" toont alles en wordt onthouden.
- Kaart "Auto laden" vernieuwd: auto met laadpaal, per auto het percentage en het model, één knop Koppelen, en een waarschuwing om slim laden in een andere app eerst uit te zetten.
- Uitleg: onderwerpen als schuifbare knoppen bovenaan. Telefoonwidget in de nieuwe stijl.

## 0.29.4 – Geen dubbele meldingen
- Opgelost: na een update of herstart, of als de verbinding met de machine even wegviel, kwam de melding "Wasmachine staat klaar" opnieuw. Zonnestuur onthoudt nu wat hij al gemeld heeft.

## 0.29.1 – Duidelijk als de machine niet bereikbaar is
- Valt de verbinding met de wasmachine of droger weg (bijv. de Miele-cloud), dan zegt Zonnestuur dat, in plaats van "wacht tot je hem klaarzet". Het plan blijft staan.

## 0.29.0 – Nieuw dashboard
- Strakker en rustiger: één accentkleur, grote koppen en bedragen, geen overbodige tekst.
- Bovenaan: het oordeel, wat je deze maand bespaarde (met vandaag, dit jaar en het tempo per jaar) en hooguit drie tips met hun opbrengst.
- Nieuwe laadkaart voor de auto: laadpaal, kabel en auto. Zit de stekker erin, dan lichten kabel en puntjes op; tijdens het laden lopen de puntjes naar de auto. De accu staat als band in de auto, met doel en minimum.
- Nieuwe pagina Uitleg (vraagteken rechtsboven en bij elke kaart): alle uitleg op één plek, ook hoe de besparing berekend wordt.

## 0.28.4 – Programma gekozen in de Miele-app wordt herkend
- Opgelost: koos je het programma in de Miele-app, dan zei Zonnestuur toch "kies nog een programma". Een startknop die nog nooit is ingedrukt heeft in Home Assistant de toestand 'onbekend'; dat telt nu als klaar. Staat de machine op 'geprogrammeerd', dan ook.

## 0.28.3 – Voorwaarden uit Home Assistant
- Per apparaat: "niet opwarmen zolang deze aan staat" (bijv. ontvochtigen) en "alleen 's nachts zolang deze aan staat" (bijv. koelseizoen), met schakelaars uit Home Assistant.
- Lager tijdens dure stroom: tot 15 °C, en instelbaar vanaf welk deel van de dag het 'duur' is.

## 0.28.2 – Wasbeurten zichtbaar
- Opgelost: een wasbeurt die Zonnestuur startte, werd niet afgesloten. Er stond alleen "Aan om …" in het logboek, zonder verbruik en zonder verslag op de kaart. Zonnestuur volgt de wasbeurt nu via de machine zelf (Miele, Home Connect): wanneer hij draait, wanneer hij klaar is en hoeveel kWh hij gebruikte (Miele-energiemeter).
- Bij klaar: een regel in het logboek ("Wasmachine klaar om 14:59, na 1 u 59 min: 0,5 kWh") en het verslag op de kaart.
- Liep er een wasbeurt af tijdens een herstart of update, dan haalt Zonnestuur het verbruik achteraf uit Home Assistant en boekt het alsnog.

## 0.28.1 – Eerlijkere uitleg
- Moest een apparaat op een duur moment aan om op tijd klaar te zijn, dan stond er "bij de goedkoopste 96% van vandaag". Nu staat er dat het niet goedkoop is, maar wel het goedkoopste moment dat er vóór de klaar-tijd nog was.

## 0.28.0 – Per kwartier plannen, slimmere voorspelling, telefoonwidget
- Opgelost: de kwartierprijzen van morgen werden niet opgehaald (alleen die van vandaag), waardoor 's avonds niet over de nacht heen gepland kon worden.
- De auto, elektrische boilers en de batterij plannen nu per kwartier in plaats van per uur: binnen een uur verschilt de prijs per kwartier. De batterij rekent de eerste 12 uur per kwartier, daarna per uur. Warmtepompen, boilers met een temperatuur-instelling en witgoed blijven per uur of blok (niet te vaak aan en uit).
- Verbruiksvoorspelling volgt veranderingen sneller: naast het geleerde profiel tellen dezelfde dag vorige week en gisteren mee.
- Zonvoorspelling: de voorzichtige (P10) en zonnige kant (P90) worden per uur van de dag geleerd. Komt er veel zon, dan laadt de batterij 's nachts niet zo vol van het net dat die zon straks naar het net moet.
- Batterij: vul aankoopprijs en aantal cycli in, dan rekent Zonnestuur de slijtage per kWh zelf uit. In de zomer laadt hij alleen van het net als het duidelijk loont.
- Telefoonwidget: een kleine pagina (Zonnestuur → Telefoonwidget) voor op je beginscherm, en nieuwe sensoren in Home Assistant voor de widgets van de Home Assistant-app: prijs nu, beste moment, wat er hierna gebeurt, tip van vandaag.
- Geplande batterijstand als stippellijn in de prijsgrafiek.
- Contractvergelijking: per leverancier ook wat je de afgelopen 30 dagen had betaald, naast je eigen kosten.
- Leesbaarheid: grafiekkleuren minstens 3:1, dure uren ook gestreept (kleurenblind), knoppen minstens 44 px op aanraakschermen, wandscherm sterker contrast.

## 0.27.0 – Weer, kwartierprijzen en slimmer warmte en laden
- Weertip: is vandaag duidelijk zonniger dan de komende dagen, dan zegt Zonnestuur 's ochtends "doe vandaag de was, laat de auto vandaag laden" (op het dashboard, op de kaart van het apparaat en als melding). Zonder panelen: een tip als vandaag goedkoper is dan morgen.
- Weer en zon voor vandaag en de komende dagen in de planningskaart; de verwachte zon staat ook in de prijsgrafiek.
- Kwartierprijzen: de grafiek toont de prijs per kwartier en witgoed start op het goedkoopste kwartier binnen het geplande uur.
- Voorzichtiger zonvoorspelling: de garantie rekent met een slechte dag (P10) die Zonnestuur uit je eigen panelen leert, en stelt de voorspelling van vandaag bij met wat je panelen nu echt leveren.
- Auto op zon: wisselt (als je laadpaal dat kan) tussen 1 fase (vanaf ± 1,4 kW zon) en 3 fasen; kies hoeveel zon er minimaal in moet (bijv. 50% bij wisselend weer).
- Warm water en verwarming: alleen verschuiven als het prijsverschil het rendementsverlies dekt; optioneel een wekelijkse legionellaronde op het zonnigste of goedkoopste uur; optioneel 1 °C lager in de duurste uren.
- Sturingsprofiel: zelfconsumptie, prijs of netvriendelijk (niets inplannen tussen 17 en 21 uur of in dure nettarief-tijdvakken).
- Terugleverkosten per kWh, als staffel of als vast bedrag; nettarief per tijdvak (vanaf 2028) instelbaar. De planning rekent ermee.
- Wat het begrenzen van de panelen bij een negatieve prijs scheelde, telt mee in de opbrengst.
- Verslag van de laatste keer op elke apparaatkaart (kWh, aandeel zon, wat het scheelde).
- Knop "Eenvoudig": alleen wat nu belangrijk is. De stroom-animatie kan op pauze; betere leesbaarheid van grijze tekst.

## 0.26.0 – Duidelijk wat nu slim is, en wat er komt
- Bovenaan een helder oordeel in drie stappen (goed moment, neutraal, liever wachten), met icoon en tekst, en het goedkoopste blok of de volgende zon.
- Nieuwe kaart "Wat Zonnestuur de komende 24 uur doet": per apparaat en batterij wanneer, waarom en wat het ongeveer kost.
- Rustigere bediening op de apparaatkaarten: Automatisch is de standaard, ingrijpen is een tweede keuze.

## 0.25.2 – Dashboard rustig
- Opgelost: de apparaatkaarten werden elke 4 seconden opnieuw opgebouwd, met de verschijn-animatie erbij. Daardoor leek het dashboard steeds opnieuw te laden. Nu alleen bijwerken als er iets verandert, en zonder animatie.

## 0.25.1 – Geen haperingen meer, TwinDos-niveau
- Opgelost: tussen 12:00 en het verschijnen van de prijzen van morgen haalde Zonnestuur elke 10 seconden de prijzen opnieuw op. Dat liet de regeling en het dashboard haperen. Nu hoogstens eens per kwartier, en altijd op de achtergrond.
- Reageert Home Assistant even niet, dan wacht Zonnestuur niet meer per apparaat 5 seconden, maar probeert het na 30 seconden opnieuw.
- Het dashboard blijft altijd reageren: is een regelronde nog bezig, dan toont het de vorige stand. Keuzelijsten op een kaart klappen niet meer dicht tijdens het kiezen.
- Wasmachine: het niveau van de TwinDos-reservoirs op de kaart, en een waarschuwing in de 'staat klaar'-melding als er een leeg is.

## 0.25.0 – Programma kiezen in Zonnestuur
- Kies het wasprogramma op de kaart van de machine (Miele en Home Connect): Zonnestuur zet het programma op het goedkoopste moment en start hem. Alleen voor deze wasbeurt, of 'altijd dit programma'.
- Programmanamen in het Nederlands; de meldingen noemen het programma.

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
