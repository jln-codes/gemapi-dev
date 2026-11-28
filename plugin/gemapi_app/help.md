# Assistant IA

Cliquer sur l’icône GEMAPI-App pour afficher le panneau, puis ouvrir **Assistant IA**
et décrire la prestation en français. Un second clic masque l’interface en conservant
la conversation et les brouillons ; le suivant restaure l’interface précédente.
Les correspondances exactes ou normalisées uniques sont prioritaires. Mistral
compare ensuite phonétiquement les noms complets, avec leur famille et leur parent.
Une correspondance phonétique complète et unique peut préremplir le brouillon ;
les autres cas demandent une clarification. Toutes les références et dates encore
manquantes sont présentées ensemble. Choisir les candidats, saisir les dates, puis
cliquer sur **Valider les corrections ensemble**. La date de fin manque ? Le champ
reste vide jusqu'à votre saisie. Rien n'est sauvegardé par cette validation.
Le récapitulatif local indique les champs reconnus et les informations encore nécessaires.
Des espaces différents ou une citation reformulée avec le parent ne demandent pas
de confirmation supplémentaire lorsque l'identité complète est vérifiée localement.
Un article extérieur au nom est accepté si la référence est unique. Deux références
identiques après normalisation restent à départager.
Un prénom seul, un nom incomplet ou des homonymes non départagés restent à préciser.
Une période explicite (par exemple « du 19 au 21 novembre 2026 ») renseigne les deux
dates après contrôle dans votre message. Une fin explicitement annoncée est
acceptée même si sa citation a été légèrement reformulée. Une fin prévue est acceptée pour une prestation
planifiée ou en cours et apparaît comme prévue. Le seul statut Terminé ne fournit
jamais une date de fin. Une date absente, incertaine ou contradictoire reste à vérifier.
Si un nom source est invérifiable, les autres champs restent dans le brouillon ;
seule la référence concernée demande un choix dans les listes, sans sélection automatique.
Décrire un bassin ou des dépôts ne constitue pas une demande de géométrie propre.
**Ouvrir le formulaire de création prérempli** ne sauvegarde rien : vérifier puis utiliser
**Enregistrer la prestation** dans le formulaire habituel. Le bouton de retour restitue
la conversation, conservée en mémoire pendant toute la session du plugin.

**Nouvelle prestation** (commande discrète en bas, raccourci **Alt+N** également
dans le formulaire) interrompt la dictée et les requêtes, puis prépare un brouillon
vide. Une seule confirmation protège une saisie non enregistrée. Refuser conserve
le brouillon et le formulaire. Les échanges restent visibles, séparés par
« Prestation abandonnée — nouvelle prestation », et ne sont pas réutilisés comme
contexte de la prochaine demande. Une sauvegarde réussie du formulaire issu de
l’assistant prépare automatiquement le brouillon suivant, sans confirmation ni
modification de la prestation déjà enregistrée.

**🎙 Dicter** active le microphone par défaut. **■ Arrêter et transcrire** envoie
le WAV à Voxtral ; **Annuler dictée** arrête sans envoyer. Après 60 secondes, la
capture s'arrête et l'audio est transcrit automatiquement. L'indicateur rouge
signale la capture ; pendant l'envoi, **Interrompre Voxtral** arrête la requête.
Le texte reconnu apparaît dans la saisie modifiable et dans l'historique.
Corriger puis cliquer sur **Envoyer** pour Mistral Small : il n'y a pas de soumission
automatique. Le clavier conserve le même brouillon. L'audio reste uniquement en
mémoire. En cas de microphone inaccessible, vérifier les autorisations de QGIS
et l'entrée par défaut du système, ou continuer au clavier.

**Réponses vocales**, désactivé par défaut, lit une phrase courte après la
vérification locale d'une proposition. Ce choix est conservé dans le profil QGIS.
Le récapitulatif texte reste affiché. **Arrêter la lecture** interrompt l'audio et
la requête TTS en cours. **Relire** réutilise la dernière réponse audio en mémoire,
ou la redemande à Voxtral si nécessaire, sans nouvel appel à Mistral Small.
Seule la courte phrase est transmise au TTS, sans noms, référentiels, UUID ou géométries.
Une nouvelle demande, une correction, la dictée, un nouveau brouillon ou le masquage
de l'assistant arrêtent l'ancienne réponse. Aucun audio n'est écrit sur disque.
Sans clé, réseau ou sortie audio utilisable, continuer au clavier : le brouillon
et le formulaire restent disponibles. Une réponse vocale ne sauvegarde jamais la prestation.

Seuls les ouvrages hydrauliques, stockages hydrauliques et enfants directs de sites (mobilier, cheminements et végétation) sont acceptés,
avec un seul objet et une géométrie propre `NULL`. Aucun champ obligatoire n'est déduit
arbitrairement. Les messages et noms complets des référentiels actifs et des trois
familles patrimoniales, avec leurs parents AH/sites, sont transmis à Mistral.
Les UUID, secrets et géométries restent locaux. Sélectionner le fichier privé avec **Fichier de clé Mistral…**
après installation ; la clé reste hors du plugin et de son ZIP. En cas d'échec ou de délai
dépassé, réessayer le message. Les appels Mistral ne bloquent pas la fenêtre.

# Aide — GEMAPI-App

Le plugin permet de créer, consulter, modifier et supprimer les prestations réalisées sur les cours d'eau, digues, aménagements hydrauliques et sites terrestres de la CABBALR, ainsi que de produire des rapports PDF selon les droits PostgreSQL du compte connecté.

La description est proposée dans la même analyse : elle conserve les précisions
métier complémentaires, sans répéter les champs structurés. Elle reste modifiable
dans le formulaire standard avant sauvegarde humaine.

## Navigation et retour à la carte

L’accueil compact est intégré en haut à droite du canevas, sans barre de titre
ni déplacement manuel. Il suit le redimensionnement et laisse la carte interactive
en dehors du panneau.
L’icône principale est cochable : un clic affiche le panneau, un second masque
les interfaces en conservant la saisie. Un nouveau clic restaure l’interface
précédente ; l’état enfoncé suit les fenêtres réellement visibles.
Il propose Créer une prestation, Gérer les prestations, Éditer un rapport,
Assistant IA et Aide.
Une seule fenêtre fonctionnelle est visible à la fois. Fermer une fiche non
sauvegardée revient à son écran d’origine après confirmation si nécessaire.
Après une sauvegarde réussie, le formulaire est masqué : la carte se centre sur
la géométrie effective (y compris celle héritée du patrimoine) et l’accueil
réapparaît. Sans géométrie exploitable, l’emprise est conservée et un message
non bloquant l’indique. Le plugin, la connexion et les caches restent chargés.
Créer une prestation démarre une nouvelle saisie ; une fiche d’édition est
relue à sa réouverture pour respecter le contrôle de version.

## Créer une prestation

**1. Localiser l'intervention**

Dessinez un point, une ligne ou un polygone. Les géométries multiples (multipoints, multilignes, multipolygones) sont également autorisées.

La géométrie est :

- **Obligatoire** pour les cours d'eau, digues, sections AH et sites terrestres parents.
- **Recommandée** pour les aménagements hydrauliques.
- **Facultative** pour le mobilier, les cheminements et la végétation. La carte utilise la géométrie du patrimoine lorsque celle de la prestation est vide.

Le plugin assure le tracé Point, Ligne, Polygone et multipartie. L'accrochage
aux sommets et segments relève de la configuration native du projet QGIS : il
est désactivé dans les projets livrés et peut être activé dans QGIS si le
travail exige des sommets coïncidents. Le filtre **Distance** et la sélection
de localisation du plugin fonctionnent séparément de cet accrochage.

**2. Choisir le patrimoine**

Chaque prestation doit être rattachée à **un seul objet patrimonial**.

- **Cours d'eau et digues :** rattachement direct à l'objet concerné.
- **Aménagements hydrauliques (AH) :** rattachement direct à une section précise, un stockage ou un ouvrage, et non à l'AH parent. Une section nécessite une géométrie propre d'intervention ; sa référence complète n'est jamais copiée.
- **Sites terrestres :** rattachement au site parent ou à un mobilier, un cheminement ou une végétation du site. Si plusieurs enfants sont concernées, choisissez le site parent.

Pour un site parent, le plugin enregistre le dessin en MultiPoint, MultiLineString
ou MultiPolygon, même avec une seule partie. La mesure cartographiée couvre
toutes les parties, y compris hors du site. La quantité déclarée reste distincte.

Le filtre **Distance** utilise le rayon configuré (500 m au départ). Il s'active
automatiquement après un dessin ou un clic avec **Activer la sélection sur la carte**.
Le dessin utilise toute la géométrie de la prestation ; le clic utilise un point
temporaire et ne modifie pas cette géométrie. Le dernier dessin ou clic définit
la référence. Modifier le rayon recalcule les propositions ; décocher Distance
suspend la restriction. Sans géométrie ni point cliqué, Distance est inactif.
Les propositions ne créent aucun rattachement automatiquement.

Les libellés placent le cours d'eau en premier : `Clarence (CL5)` ou
`La Lawe (ZEC Gosnay1)`. Ces deux derniers modes enregistrent respectivement
l'UUID de la digue et celui de la section AH, jamais l'UUID du cours d'eau ou
celui de l'AH parent. Plusieurs sections du même AH sur une rivière sont
identifiées par leurs bornes longitudinales ; leurs UUID restent distincts.
Les stockages et ouvrages affichent leur nature et leur nom propres.

La proximité ne prouve pas une substitution : les fractions, la signature et
la qualification de la référence sont contrôlées. Le cours d'eau reste proposé
hors des portions couvertes. Les limites, chevauchements et géométries couvrant
plusieurs supports sont signalés ; aucun choix automatique entre digue et
section AH n'est effectué. Précisez ou subdivisez l'intervention avant
l'enregistrement. Les associations historiques ne sont jamais converties ; une
référence devenue obsolète reste affichée avec une indication de revalidation.
La vérification à l'enregistrement s'applique aussi lorsque Distance est désactivé.

**3. Renseigner la prestation**

Complétez les champs obligatoires (*), puis les informations utiles : dates, intervenants, nature des travaux, description, etc.

Vous pouvez ajouter jusqu'à deux photographies.

Enregistrez la prestation.

## Gérer les prestations

Recherchez une prestation à l'aide des filtres, puis ouvrez sa fiche pour la consulter ou la modifier.

Le profil Lecture ouvre la fiche en consultation seule. Les profils disposant
du droit `UPDATE` peuvent la modifier. Pour une prestation visible sur la carte,
l'action **Modifier cette prestation** des couches points, lignes et polygones
ouvre directement le même formulaire ; fermez-le pour revenir à QGIS. La
consultation des huit mesures stockées reste disponible dans le formulaire
QGIS natif des couches cartographiques et dans `prestations_fiches`.

Avec le droit `DELETE`, sélectionnez une prestation dans le gestionnaire et
cliquez sur **Supprimer**. Vérifiez le libellé et l'UUID dans la confirmation :
la suppression est définitive. **Non** annule sans écriture. Si la prestation
a été modifiée ou supprimée depuis la recherche, actualisez la liste avant
de recommencer. La prestation est supprimée seule ; ses objets patrimoniaux,
ses référentiels et les fichiers photographiques sont conservés.

## Produire un rapport

Deux formats sont disponibles :

- **Fiches de prestations** : fiches détaillées avec carte et photographies.
- **Rapport synthétique** : récapitulatif avec filtres et regroupements personnalisables.

Dans un rapport du parent **AH**, une prestation rattachée à un cours d’eau
peut apparaître avec une mention de **reconnaissance indirecte** : sa géométrie
propre intersecte exactement une section conforme de cet AH sur le même cours
d’eau qualifié. Aucun rayon de proximité n’intervient. Le rattachement direct,
les quantités et la géométrie restent inchangés. La fiche indique le nom du cours
d’eau et les noms des AH concernés dans son bloc de rattachement, sans identifiant
technique ni page supplémentaire.

Les rapports d’un **stockage** ou d’un **ouvrage** ne reçoivent que leurs
prestations directement rattachées. Un rapport du cours d’eau conserve toutes
ses prestations directes ainsi que celles rattachées à ses sections AH. Le parent AH retrouve aussi les prestations de ses sections enregistrées, indépendamment de leur conformité actuelle. Plusieurs AH peuvent partager une prestation reconnue indirectement : les
sous-totaux AH se recouvrent alors, tandis que le total général compte
chaque prestation une seule fois. Les quantités ne sont pas réparties entre AH.

## Utilisation dans QGIS

Les couches et tables attributaires permettent de consulter les données et leurs relations.

**Privilégiez le plugin pour la saisie et les modifications.** L'édition directe dans QGIS peut être limitée par les contraintes relationnelles ; certaines vues sont en lecture seule.

Le plugin nécessite une connexion PostgreSQL/PostGIS. Les fichiers Shapefile et GeoPackage du serveur partagé ne constituent pas sa base de données.


## Sections hydrographiques d’un AH

Ouvrez la fiche native du parent **AH** dans le projet de démonstration courant.
Le bloc **Sections hydrographiques** présente les références acceptées. Le bouton
Tableau du widget natif affiche cours d’eau, fractions, longueur figée et état ;
le bouton de zoom les localise sans décaler leur géométrie.

**Rechercher des sections proposées…** ouvre une couche temporaire et la table
QGIS des candidats. Les actions de chaque ligne permettent de localiser, accepter
ou refuser la proposition. Aucune intersection n’est enregistrée par la recherche.
**Enregistrer la référence sur le serveur** confirme explicitement l’acceptation.
Annuler cette fenêtre n’écrit rien ; refuser ne retire que la proposition temporaire.

Depuis la fiche AH, **Créer une section manuelle…** permet de choisir le cours
d'eau et de saisir les fractions de début/fin. Prévisualisez la section calculée
avant de confirmer. Elle peut dépasser le stockage, et un AH sans stockage peut
être référencé. Les intersections proposées restent une aide facultative.

Dans la couche **Sections hydrographiques AH**, l’action **Modifier / revalider les
fractions…** permet de changer le support et les fractions puis de confirmer une
nouvelle révision. Seule une modification du cours d'eau périme la référence. Les
références non conformes gardent leur géométrie figée. Une orientation non vérifiée
ou incorrecte interdit toute validation ; le plugin ne la corrige jamais.

Ces confirmations sont enregistrées séparément de la fiche AH. Fermer ensuite
la fiche ou annuler une autre édition ne les annule pas. Terminez toute session
d’édition PostgreSQL de la même connexion avant de valider une référence : le
plugin ne valide ni n’annule vos autres changements.

## Créer et gérer une digue

Utiliser le menu **GEMAPI-App → Créer une digue…**. Renseigner le nom,
l'identifiant externe et le commentaire éventuels, puis le cours d'eau qualifié,
la rive et les fractions. **Prévisualiser sur la carte**, examiner le tracé, puis
**Confirmer et enregistrer** crée atomiquement la digue et son repère.

Dans la table ou la fiche `digues_carte`, les actions **Modifier le repère…**,
**Revalider le repère…** et **Supprimer la digue…** s'utilisent sans activer le
crayon. Revalider exige une nouvelle prévisualisation et conserve les fractions.
Le compte Patrimoine est requis pour écrire. Une prestation rattachée bloque la
suppression. Fermer ou annuler le dialogue ne produit aucune écriture.

La base doit avoir reçu la mise à jour des digues QGIS-EDIT-02 ; contacter
l'administrateur si le plugin signale qu'elle est absente.
