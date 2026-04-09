# Audit Système — Modules Réparation Hi-Fi
**Date : 23 mars 2026**

---

## 1. Diagnostic Général

Le système est fonctionnel et couvre bien le cycle de réparation. L'architecture à deux modules (catalogue + workflow) est saine. Cependant, après analyse approfondie, plusieurs faiblesses structurelles et opérationnelles ressortent :

**Forces :**
- Traçabilité appareil solide (stock.lot + historique réparations + garantie SAR/SAV)
- Système de batch bien pensé pour les dépôts multi-appareils
- Séparation claire des rôles technicien/manager/admin
- Kiosque atelier avec identification employé

**Faiblesses principales :**

1. **Déconnexion Vente ↔ Réparation ↔ Stock** — Les trois modules coexistent mais ne communiquent pas assez. Le stock ne reflète pas toujours la réalité terrain. Les ventes d'équipement et les factures réparation vivent dans des silos séparés.

2. **Workflow devis trop rigide et trop fragmenté** — Le processus devis exige beaucoup d'allers-retours manuels entre technicien et manager. Pas de suivi du temps d'attente client.

3. **Pas de gestion des pièces détachées dans le flux de réparation** — Les pièces apparaissent uniquement au moment de la facturation, pas pendant l'intervention. Aucun lien avec le stock réel de pièces.

4. **UX formulaire réparation surchargé** — Le formulaire manager a trop de champs visibles simultanément. Le technicien doit naviguer entre onglets pour des actions courantes.

5. **Aucun suivi inter-sites** — Malgré 3 localisations physiques, le système ne gère pas le transfert d'appareils entre sites ni la file d'attente par site.

---

## 2. Opportunités d'Amélioration (Top 10)

### 2.1. Automatiser l'affectation technicien + file d'attente

**Problème :** Le technicien doit manuellement "prendre" une réparation dans la liste. Pas de vision claire de la charge par technicien. Les réparations urgentes ne sont pas mises en avant efficacement.

**Impact réel :** Temps perdu à chercher quoi faire. Risque d'oublier des réparations urgentes ou anciennes.

**Amélioration proposée :**
- Ajouter un champ computed `waiting_days` (jours depuis `entry_date`) visible dans la liste et le kanban
- Trier par défaut : urgent d'abord, puis par ancienneté
- Ajouter un compteur de charge par technicien dans le dashboard (nombre de réparations `under_repair` par technicien)
- Optionnel : bouton "Prendre le suivant" qui affecte automatiquement la plus ancienne réparation `confirmed`

**Bénéfice :** Réduction du temps mort, meilleure répartition de la charge.
**Complexité :** Faible

---

### 2.2. Suivi des pièces détachées pendant la réparation

**Problème :** Les pièces sont saisies uniquement à la facturation (`repair.pricing.part`). Le technicien ne peut pas noter les pièces utilisées pendant qu'il travaille. Si la facturation est faite plus tard par le manager, l'information est perdue ou transmise oralement.

**Impact réel :** Erreurs de facturation. Stock de pièces non suivi. Impossibilité de connaître la consommation réelle par catégorie d'appareil.

**Amélioration proposée :**
- Ajouter un onglet "Pièces utilisées" directement sur la réparation (modèle `repair.part.line` lié à `repair.order`)
- Le technicien y note les pièces pendant la réparation
- Le wizard de facturation pré-remplit `extra_parts_ids` depuis ces lignes
- Optionnel : décrémente automatiquement le stock de pièces si elles sont gérées dans Odoo stock

**Bénéfice :** Traçabilité des pièces, facturation plus précise, données pour anticiper les achats.
**Complexité :** Moyenne

---

### 2.3. Simplifier le workflow devis

**Problème :** Le processus actuel est en 5-6 étapes :
1. Technicien diagnostique
2. Technicien clique "Etablir un devis" → `quote_state = pending`
3. Manager reçoit une activité
4. Manager ouvre le wizard pricing, crée le devis (sale.order)
5. Manager envoie le devis au client (manuellement)
6. Client répond → Manager valide manuellement (`quote_state = approved`)

Aucune notification au client, aucun suivi du temps d'attente.

**Impact réel :** Délai excessif sur les réparations avec devis. Le client attend sans savoir. Le manager doit se souvenir de relancer.

**Amélioration proposée :**
- Ajouter `quote_sent_date` et `quote_response_date` pour mesurer les délais
- À la création du sale.order via le wizard, proposer l'envoi par email en un clic (action `action_quotation_send` du sale.order)
- Quand le sale.order passe en `sale` (client confirme en ligne via le portail Odoo), auto-setter `quote_state = approved` sur la réparation
- Ajouter une activité planifiée de relance automatique (cron) si pas de réponse après X jours

**Bénéfice :** Cycle devis divisé par 2. Moins d'oublis. Meilleure expérience client.
**Complexité :** Moyenne

---

### 2.4. Consolider le tableau de bord atelier

**Problème :** Le dashboard actuel affiche 7 tuiles avec des compteurs. C'est informatif mais pas actionnable. Le technicien doit quand même naviguer vers la liste pour agir.

**Impact réel :** Le dashboard est un "écran d'accueil" plutôt qu'un outil de travail.

**Amélioration proposée :**
- Remplacer le dashboard par une vue kanban groupée par état (`confirmed` / `under_repair` / `done`) directement comme écran principal de l'atelier
- Conserver les tuiles comme en-tête/résumé au-dessus du kanban (via une vue composite ou un widget OWL)
- Permettre le drag & drop entre colonnes pour changer d'état (confirmer → démarrer → terminer)

**Bénéfice :** Un seul écran pour tout. Moins de navigation.
**Complexité :** Moyenne (le kanban groupé est natif Odoo, mais le combiner avec les tuiles demande du JS)

---

### 2.5. Gérer les transferts inter-sites

**Problème :** Avec 3 sites (Paris, Bourg-la-Reine, Forges), les appareils se déplacent physiquement. Le système ne trace que la localisation d'entrée (`pickup_location_id`). Aucune notion de "cet appareil a été transféré de Paris à l'atelier principal".

**Impact réel :** On ne sait pas où est un appareil à un instant T. Le directeur qui se déplace entre les sites n'a pas de vision globale.

**Amélioration proposée :**
- Utiliser les emplacements stock existants (un par site) + les pickings internes pour tracer les transferts
- Ajouter un bouton "Transférer vers..." sur la réparation qui crée un picking interne
- Ajouter un champ computed `current_location` basé sur `lot_id.location_id` plutôt que sur `pickup_location_id`
- Dashboard : filtre par localisation actuelle

**Bénéfice :** Savoir où est chaque appareil. Coordination inter-sites.
**Complexité :** Moyenne

---

### 2.6. Améliorer la gestion des abandons

**Problème :** Quand un client abandonne son appareil, le wizard `device.stock.wizard` le fait entrer en stock. Mais ensuite ? L'appareil est en stock sans suivi particulier. Pas de process pour décider : on le revend ? on le recycle ? on le garde pour pièces ?

**Impact réel :** Accumulation d'appareils abandonnés sans suivi. Opportunité de revente manquée.

**Amélioration proposée :**
- Ajouter un état `abandoned_stock` sur le lot (distinct de `stock`) pour les différencier
- Vue filtrée "Appareils Abandonnés" avec date d'abandon, ancien propriétaire, état fonctionnel
- Optionnel : workflow simple (abandonné → testé → mis en vente / recyclé)

**Bénéfice :** Visibilité sur le stock d'abandons. Potentiel de revente.
**Complexité :** Faible

---

### 2.7. Notifications client automatisées

**Problème :** Le client n'est informé de rien sauf s'il utilise le lien de tracking (qui est passif — il doit aller le consulter). Aucune notification par email ou SMS aux étapes clés.

**Impact réel :** Appels entrants "où en est ma réparation ?". Charge de travail support pour le manager.

**Amélioration proposée :**
- Configurer des mail templates automatiques sur les transitions d'état :
  - `confirmed` → "Nous avons bien reçu votre appareil, réf. XXX"
  - `done` → "Votre appareil est réparé, vous pouvez venir le chercher"
  - `irreparable` → "Malheureusement, votre appareil n'est pas réparable"
- Utiliser le mécanisme `mail.template` + `mail.thread` déjà en place
- Inclure le lien de tracking dans chaque email

**Bénéfice :** Réduction drastique des appels "où en est-on". Professionnalisme.
**Complexité :** Faible

---

### 2.8. Refactoring du write() override

**Problème :** Le `write()` override dans `repair_order.py:414` supprime silencieusement des valeurs du dict `vals` pour les champs protégés. C'est un anti-pattern dangereux :
- Pas de `UserError` levée → le manager croit avoir sauvegardé mais rien ne s'est passé
- `del vals[field]` modifie le dict en place → effets de bord possibles
- La logique de reset technicien sur retour à `draft` est mélangée avec la sécurité

**Impact réel :** Bugs silencieux. Données pas sauvegardées sans avertissement.

**Amélioration proposée :**
- Lever une `UserError` explicite si un utilisateur non autorisé tente de modifier un champ protégé
- Séparer la logique de sécurité (groupes `ir.rule` ou `@api.constrains`) de la logique métier (reset technicien)
- Utiliser des `ir.rule` record rules pour le contrôle d'accès plutôt que du code Python

**Bénéfice :** Moins de bugs silencieux. Code plus maintenable.
**Complexité :** Faible

---

### 2.9. Unifier la facturation réparation ↔ vente

**Problème :** Deux chemins de facturation coexistent :
1. Wizard pricing → facture directe (`account.move`)
2. Wizard pricing → devis (`sale.order`) → confirmation → facture

Le premier court-circuite complètement le module vente. Les factures directes n'ont pas de bon de commande, pas de position fiscale auto-détectée par le standard Odoo, pas de suivi dans le pipeline commercial.

**Impact réel :** Reporting commercial incomplet. Complexité de maintenance (deux chemins à maintenir).

**Amélioration proposée :**
- Privilégier systématiquement le chemin sale.order → facture (c'est le standard Odoo)
- Le wizard ne crée plus que des sale.order (type `repair_quote`)
- La facturation se fait via le flux standard sale.order → `action_create_invoices()`
- Conserver la facture directe uniquement pour les cas exceptionnels (petites réparations < 50€ par exemple)

**Bénéfice :** Un seul flux. Reporting unifié. Moins de code à maintenir.
**Complexité :** Moyenne

---

### 2.10. Améliorer la recherche et le filtrage

**Problème :** La vue recherche manager est correcte mais manque de filtres essentiels :
- Pas de filtre "En retard" (réparations anciennes sans avancement)
- Pas de filtre par marque d'appareil
- Pas de filtre "Avec devis en attente de réponse client"
- Pas de recherche par numéro de série

**Impact réel :** Le manager perd du temps à chercher des réparations spécifiques.

**Amélioration proposée :**
- Ajouter les filtres manquants dans la vue recherche
- Ajouter un filtre "Plus de 7 jours sans action" (basé sur `write_date`)
- Rendre `serial_number` et `lot_id.name` cherchables
- Ajouter un group by "Marque" (via `product_tmpl_id.brand_id`)

**Bénéfice :** Accès plus rapide aux informations critiques.
**Complexité :** Faible

---

## 3. Améliorations Structurelles / Architecturales

### 3.1. Modèle de données

**`repair_extensions.py` est trop gros (586 lignes, 7 modèles hérités dans un seul fichier).**
C'est le fichier le plus critique et le plus fragile du système. Chaque modification touche potentiellement tous les modèles. Recommandation :
- Séparer en fichiers dédiés : `stock_lot_extensions.py`, `sale_order_extensions.py`, `account_move_extensions.py`, etc.
- Pas de changement fonctionnel, juste de l'organisation.

**Le champ `import_state` est un reliquat de migration.** S'il n'est plus utilisé, le supprimer.

**`technician_user_id` vs `technician_employee_id` — redondance.** On a un champ `hr.employee` (le vrai) et un champ `res.users` (jamais utilisé dans les vues, seulement dans le write override). Simplifier en ne gardant que `technician_employee_id` et en calculant le user via `employee.user_id` quand nécessaire.

### 3.2. Workflow

**Le champ `delivery_state` devrait être une extension du `state` principal plutôt qu'un état parallèle.** Actuellement, une réparation `done` peut être en `delivery_state = none` (en atelier), `delivered`, ou `abandoned`. Cela crée une matrice d'états 6×3 dont beaucoup de combinaisons sont invalides. La logique de vérification `if self.delivery_state == 'abandoned'` est dupliquée dans presque toutes les méthodes d'action.

Alternative : ajouter `delivered` et `abandoned` comme états finaux dans le `state` principal, après `done`. Le flux devient linéaire : `draft → confirmed → under_repair → done → delivered` (ou `→ abandoned`).

### 3.3. Sécurité — `ir.model.access.csv`

**Le wizard de pricing n'est accessible qu'aux admins.** Pourtant c'est le manager qui facture au quotidien. Ajouter l'accès pour `group_repair_manager`.

**Duplicate ID détecté** dans le CSV (`access_repair_manager` utilisé deux fois). Risque de conflit au chargement.

### 3.4. Intégration Stock

Le système crée des pickings via `_create_repair_picking()` qui sont auto-validés immédiatement (`skip_backorder=True`). C'est correct pour le tracking lot, mais :
- **Aucune vérification de quantité disponible avant le move.** Si le quant n'existe pas, on le "seed" au customer location, ce qui est un contournement.
- **Les pickings créés ne sont liés à aucun document source** (pas de `sale_id`, pas de champ custom). Ils sont difficiles à retrouver dans l'historique stock.

Recommandation : ajouter un champ `repair_id` sur `stock.picking` pour la traçabilité, et afficher les pickings liés sur le formulaire réparation.

---

## 4. Quick Wins (Fort ROI, Effort Faible)

| # | Amélioration | Effort | Impact |
|---|-------------|--------|--------|
| 1 | **Ajouter `waiting_days`** (computed: `today - entry_date`) visible dans les listes et kanban | 1h | Visibilité immédiate sur les réparations en retard |
| 2 | **Notifications email automatiques** sur `confirmed` et `done` via mail.template | 2h | Moins d'appels clients, image pro |
| 3 | **Filtres de recherche manquants** : numéro de série, marque, "en retard > 7j" | 1h | Gain de temps quotidien pour le manager |
| 4 | **Corriger l'accès pricing wizard** pour le groupe manager | 5min | Le manager peut facturer sans être admin |
| 5 | **Corriger le duplicate ID** dans le CSV de sécurité | 5min | Éviter un bug de chargement |
| 6 | **Afficher `entry_date` avec l'heure** dans le formulaire (pas juste la date) | 5min | Meilleur suivi intra-journée, notamment à Paris |
| 7 | **Ajouter un bouton "Copier le lien de tracking"** visible dans le formulaire | 30min | Le manager peut envoyer le lien au client facilement |
| 8 | **Trier le kanban atelier par ancienneté** (`entry_date asc`) plutôt que par priorité desc | 5min | Les plus vieux appareils sont traités en premier |
| 9 | **Supprimer `technician_user_id`** (champ inutilisé dans les vues) | 15min | Simplification du modèle |
| 10 | **Lever une UserError dans le write()** au lieu de supprimer silencieusement les champs protégés | 15min | Éviter les bugs silencieux |

---

## Synthèse des Priorités

**Court terme (cette semaine) :** Quick wins 1-6 — aucun risque, impact immédiat.

**Moyen terme (1-2 semaines) :**
- Notifications client automatisées (§2.7)
- Simplification workflow devis (§2.3)
- Séparation de `repair_extensions.py` (§3.1)

**Long terme (1 mois+) :**
- Suivi pièces détachées (§2.2)
- Transferts inter-sites (§2.5)
- Unification facturation (§2.9)
- Refonte `state` + `delivery_state` (§3.2)
