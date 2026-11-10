# Categorising UK card merchants

Read this before proposing your first batch. The categories below are pennywise's
starter set. Run `uv run pennywise categories` for the live list, since the user may
have added their own. Merchant names in pennywise are cleaned descriptors (store
numbers and trailing town names removed), so expect things like `PRET A MANGER`,
`COSTA COFFEE`, `SQ *GAILS BAKERY`.

## Contents
- What goes where (by category)
- Tricky cases
- Reading opaque descriptors
- When to ask instead of deciding

## What goes where

**Groceries.** Supermarkets and food shops for the home: Tesco, Sainsbury's, Asda,
Morrisons, Waitrose, Aldi, Lidl, Co-op, Iceland, Ocado, M&S Food / Simply Food,
Marks & Spencer (usually food), butchers, bakers bought for home, farm shops, Abel &
Cole, Riverford, Milk & More.

**Eating out.** Food and drink you go out for: restaurants, cafés and coffee shops
(Costa, Starbucks, Caffè Nero, Pret, Gail's, Greggs), pubs and bars, Nando's,
Wagamama, Pizza Express, Côte, Dishoom, motorway services food (Welcome Break, Moto,
Roadchef), work lunches.

**Takeaway & delivery.** Food delivered or collected: Deliveroo, Just Eat, Uber Eats,
Domino's, Papa John's, local takeaways.

**Transport.** Getting around: fuel (Shell, BP, Esso, Texaco, Gulf, Jet, supermarket
petrol: Tesco Petrol, Sainsbury's Petrol, Asda Petrol, Morrisons Petrol), Transport for
London, trains (Trainline, LNER, GWR, Avanti, Southern, Thameslink, Southeastern,
ScotRail), buses and coaches (National Express, Megabus), Uber and other taxis
(Addison Lee, FreeNow, Bolt), parking (RingGo, PayByPhone, JustPark, NCP), Dart Charge,
congestion charge and ULEZ, car servicing and MOT (Kwik Fit, Halfords Autocentre),
car wash, EV charging (Pod Point, Tesla Supercharger, Osprey, Instavolt), DVLA.

**Travel & holidays.** Trips away: airlines (British Airways, easyJet, Ryanair, Jet2,
Virgin Atlantic, Wizz Air, TUI), hotels and stays (Premier Inn, Travelodge, Hilton,
Airbnb, Booking.com, Expedia, Hotels.com, Vrbo), Eurostar, holiday car hire, airport
parking and lounges, travel insurance, spending clearly made abroad (foreign place
names or currencies in the description).

**Shopping.** Things you buy that aren't groceries: Amazon, eBay, Etsy, Argos, John
Lewis, Currys, Apple Store purchases, clothes and shoes (Next, Zara, H&M, Uniqlo,
ASOS, Primark, TK Maxx, Clarks, JD Sports, Sports Direct), department stores,
electronics, books (Waterstones), stationery (WHSmith), general online retail.

**Household.** The home itself: IKEA, B&Q, Wickes, Homebase, Screwfix, Toolstation,
Dunelm, The Range, Robert Dyas, garden centres, furniture, appliances bought for the
house, cleaners, gardeners, window cleaners, tradespeople, home repairs, locksmiths.

**Bills & services.** Regular essential services: mobile (EE, O2, Vodafone, Three,
Tesco Mobile, giffgaff, Sky Mobile), broadband and TV (BT, Sky, Virgin Media,
TalkTalk), energy (Octopus, British Gas, EDF, E.ON, OVO), water, council tax, TV
licence, home/car/pet/life insurance (Aviva, Direct Line, Admiral, LV), professional
services (accountant, solicitor), Royal Mail and parcel services.

**Subscriptions.** Recurring entertainment, software and memberships: Netflix,
Spotify, Disney+, Amazon Prime, Apple (iCloud, App Store, Apple Music), Google (Google
One, YouTube Premium), Audible, Kindle Unlimited, Microsoft 365, Adobe, Dropbox,
ChatGPT/OpenAI, Anthropic/Claude, newspapers and magazines (The Times, Guardian,
Economist), Patreon, NOW, Paramount+.

**Health & beauty.** Body and wellbeing: Boots, Superdrug, pharmacies (Lloyds
Pharmacy), dentist, optician (Specsavers, Vision Express), private GP and physio,
hairdresser and barber, beauty and nail salons, Lush, The Body Shop, toiletries.

**Leisure.** Fun and activity: cinema (Odeon, Vue, Cineworld, Everyman), theatre and
gigs (Ticketmaster, See Tickets, ATG, Eventim), museums and attractions, National
Trust and English Heritage visits, gym and sport (PureGym, The Gym Group, David Lloyd,
Better, Nuffield), golf, hobbies, games (Steam, PlayStation, Xbox, Nintendo), music
and sports shops bought for a hobby, days out.

**Kids & family.** Children: school payments (ParentPay, school meals, trips),
childcare and nurseries, clubs and classes, uniforms (school uniform shops), toy shops
(Smyths, Hamleys, The Entertainer), children's activities.

**Gifts & charity.** For other people: charity donations (JustGiving, GoFundMe,
named charities), cards and flowers (Moonpig, Funky Pigeon, Interflora, Bloom &
Wild), gift shops, gift experiences (Red Letter Days, Buyagift).

**Pets.** Pets at Home, vets, pet food subscriptions (Butternut Box, Tails.com),
groomers, kennels and catteries.

**Other.** Genuinely miscellaneous spending. Not a place for things you couldn't
identify; ask about those instead.

## Tricky cases

- **Supermarket petrol stations** are Transport, not Groceries. pennywise separates
  them as `Tesco Petrol`, `Sainsbury's Petrol` and so on. A descriptor with `PFS`
  (petrol filling station) means fuel.
- **Uber vs Uber Eats.** `Uber` is Transport; `Uber Eats` is Takeaway & delivery.
- **Amazon** is Shopping. **Amazon Prime** (the membership, Prime Video) is
  Subscriptions.
- **Apple.** `Apple` (from APPLE.COM/BILL) is App Store, iCloud and other
  subscriptions, so Subscriptions. A large one-off at an Apple Store is Shopping;
  suggest an override if the user confirms.
- **Google.** `Google` charges are usually Google One, YouTube or Play subscriptions,
  so Subscriptions.
- **Marks & Spencer** is mostly food, so Groceries. If the amounts look like clothing,
  ask.
- **Boots** is Health & beauty, even though it sells other things.
- **Phone and broadband** are Bills & services; **streaming and software** are
  Subscriptions. The test: would the household be stuck without it? Then it's a bill.
- **Insurance** is Bills & services. **Travel insurance** is Travel & holidays.
- **Coffee shops** are Eating out, even for a takeaway coffee.
- **Station and airport shops** (WHSmith Travel, Boots at an airport) follow what they
  are; spending at an airport during a trip is better placed in Travel & holidays,
  but ask if it's a big amount.
- **Motorway services** are Eating out if food, Transport if fuel. Check the
  descriptor (`WELCOME BREAK` vs `SHELL`).
- **PayPal (`PAYPAL *SOMETHING`)** is categorised by the merchant after the star.
- **Card payment processors**: `SQ *` (Square), `SUMUP *`, `ZETTLE_*`, `IZ *`,
  `SP *` (Shopify) are small businesses. Categorise by the name after the prefix.
- **Refunds** need no special handling: they take their merchant's category and reduce
  it.

## Reading opaque descriptors

When a merchant name means nothing on its own:
1. Run `uv run pennywise transactions --merchant "NAME" --json` and look at the
   descriptions, amounts, dates and card member.
   - Regular same amount each month → probably a subscription or a bill.
   - Weekly small amounts → food or transport.
   - One large amount → a purchase or a trip.
2. Use what you know about UK businesses. Many descriptors are a company's legal name
   rather than its brand (`EE LIMITED`, `TFL TRAVEL CH`).
3. If it's still unclear, put it under **Need your call** with what you found ("3
   payments of £45 on the 1st of each month: a membership or a service?").

## When to ask instead of deciding

Ask the user when:
- the name is a person (`J SMITH`): it could be a tradesperson, a cleaner, a friend, or
  a refund to someone;
- the business could plausibly be two or more categories and the amounts don't settle
  it;
- the amount is large and the category would change the picture noticeably;
- you'd otherwise be choosing Other.

Asking costs the user a second; a wrong rule misfiles every charge at that merchant
until someone notices.
