# Identité visuelle de gemapi.dev

Le cartouche approuvé utilise un fond ivoire, « gemapi » en bleu, « .dev » en
vert et un symbole représentant un arbre dans une courbe d'eau. Les sources
actuelles sont des PNG (elles ne prétendent pas être vectorielles).

| Fichier | Usage |
| --- | --- |
| `cartouche.png` | Logo complet horizontal, 1200 × 400 px, fond ivoire. |
| `logo-seul.png` | Symbole sans texte, 560 × 560 px, fond transparent. |
| `favicon.ico`, `favicon-16x16.png`, `favicon-32x32.png` | Icônes de navigateur. Le ICO contient 16, 32 et 48 px. |
| `apple-touch-icon.png` | Icône Apple, 180 × 180 px. |
| `icons/icon-192.png`, `icons/icon-512.png` | Icônes PWA avec fond ivoire opaque. |
| `icons/icon-maskable-192.png`, `icons/icon-maskable-512.png` | Icônes PWA avec marge de sécurité pour les masques circulaires. |
| `site.webmanifest` | Manifeste des icônes, prévu pour une application servie à la racine du domaine. |

Les PNG dérivés et le ICO se régénèrent depuis `logo-seul.png` avec
`bash branding/build-icons.sh` (ImageMagick requis). Ne pas régénérer les
icônes depuis le cartouche : le texte et son fond n'entrent pas dans les
icônes d'application.

Lorsque le dossier `branding` est servi tel quel à `/branding/`, ajouter
dans le `<head>` :

```html
<link rel="icon" href="/branding/favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="32x32" href="/branding/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/branding/favicon-16x16.png">
<link rel="apple-touch-icon" sizes="180x180" href="/branding/apple-touch-icon.png">
<link rel="manifest" href="/branding/site.webmanifest">
<meta name="theme-color" content="#23443D">
```

Pour une application installée sous un sous-chemin, adapter `id`, `start_url`
et `scope` dans le manifeste. Le service worker et l'application elle-même
seront ajoutés lors de l'implémentation de la PWA.
