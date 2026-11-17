# Mejoras fiscales para `taxes.py` (aprendidas en Renta Bróker)

Renta Bróker empezó traduciendo a Swift la lógica de `src/plout/exporters/taxes.py`. Por el camino aparecieron reglas que Plout no aplica o aplica a medias. Este documento las recoge **ordenadas por impacto en dinero**, cada una con:

- qué hace hoy Plout (función concreta);
- qué dice la norma;
- cómo implementarlo en Python;
- casos de prueba con cifras.

Todo se refiere a la Renta 2025. Las cifras de las reglas salen de `rules-2025.json` de Renta Bróker, con su fuente oficial.

---

## 1. Deducción por doble imposición: tope del convenio y del tipo medio

**Plout hoy.** `dividends_for_year` suma toda la retención extranjera en `withholdingEur`. `build_renta_pack` la pone entera como «Impuesto satisfecho en el extranjero».

**La norma (art. 80 LIRPF y los convenios).** La deducción por cada dividendo es el menor de:

1. lo retenido de verdad en el país de origen;
2. lo que el convenio permite retener: `bruto × tipo del convenio`.

Lo retenido por encima del convenio no se deduce en España: se reclama al país de origen. Suiza retiene un 35 % y el convenio permite un 15 %, así que el 20 % restante se pide a Suiza.

Además, el total deducible no puede superar lo que España cobra por esas rentas: `rendimientos extranjeros en la base × tipo medio del ahorro`.

**Tipos del convenio para cartera (2025), por código de país:**

| Tipo | Países |
|---|---|
| 5 % | JP, SG, CY, MT |
| 10 % | CN, GR, HK, IL, MX, GB, LV |
| 15 % | US, DE, FR, NL, IE, LU, CH, BE, AT, IT, PT, CA, AU, BR, KR, FI, HU, IN, NO, NZ, PL, CZ, ZA, SE, IS, EE, LT, SI, AE |
| Sin convenio | DK (denunciado en 2008): se deduce lo retenido |

Fuentes: DGT, límites de los convenios (2018); lista de convenios de Hacienda; BOE del convenio con Japón (en vigor desde 2021).

**El país de origen** es el de la sociedad que paga. Normalmente coincide con las dos primeras letras del ISIN, pero no siempre:

- **Certificados de depósito (ADR/GDR):** el ISIN es del país que los emite, pero manda el de la sociedad.
- **ETF irlandeses:** el país es IE, aunque inviertan en EE. UU.

Cuando el país sea dudoso, conviene que el usuario lo indique.

**Implementación:**

```python
from decimal import Decimal as D

TREATY_RATES_2025 = {"US": D("0.15"), "CH": D("0.15"), "GB": D("0.10"), "JP": D("0.05"), ...}
NO_TREATY = {"DK"}

def creditable_tax(gross_eur: D, foreign_tax_eur: D, country: str) -> tuple[D, D]:
    """(deducible en España, exceso a reclamar en origen)."""
    rate = TREATY_RATES_2025.get(country)
    if rate is None:  # sin convenio o país desconocido (avisar en este segundo caso)
        return foreign_tax_eur, D(0)
    cap = round2(gross_eur * rate)
    credit = min(foreign_tax_eur, cap)
    return credit, foreign_tax_eur - credit

def double_tax_deduction(creditable_total: D, foreign_income_in_base: D, average_rate_pct: D) -> D:
    return min(creditable_total, round2(foreign_income_in_base * average_rate_pct / 100))
```

- `average_rate_pct` es `cuota del ahorro / base del ahorro × 100`, redondeado a 2 decimales.
- `foreign_income_in_base` es el bruto extranjero reducido en la misma proporción que el bloque de rendimientos, si se compensaron pérdidas contra él (apartado 3).

**Casos de prueba:**

| Dividendo | Bruto | Retenido | Deducible | A reclamar en origen |
|---|---:|---:|---:|---:|
| Nestlé (CH) | 100,00 | 35,00 | 15,00 | 20,00 |
| Apple (US, con W-8BEN) | 100,00 | 15,00 | 15,00 | 0,00 |
| Apple (US, sin W-8BEN) | 100,00 | 30,00 | 15,00 | 15,00 |
| Shell (GB) | 100,00 | 0,00 | 0,00 | 0,00 |
| Novo Nordisk (DK) | 100,00 | 27,00 | 27,00 | 0,00 |

**En el pack de Renta Web:** el apartado de dividendos lleva el bruto. En «Deducción por doble imposición internacional» van los rendimientos extranjeros y el impuesto **deducible**, no el retenido. Añade un aviso con el exceso por país para que el usuario lo reclame.

---

## 2. FIFO por ISIN, no por producto de DEGIRO

**Plout hoy.** `build_assets` usa `f"degiro:{productId}"` como clave de la cola. Si el mismo valor se compra en dos bolsas (AAPL en NASDAQ y en Xetra tienen dos `productId`), salen dos colas FIFO. Una compra manual de `manual_trades.json` o de otro bróker con el mismo ISIN también va a otra cola.

**La norma (art. 37.2 LIRPF).** Son valores homogéneos los del mismo emisor con los mismos derechos, o sea, el mismo ISIN, estén donde estén. Se venden primero los comprados antes **sumando todos los brókers**. El TEAC lo confirmó en su resolución de 19/02/2026 (RG 1535/2024).

**Implementación:**

```python
def queue_key(trade) -> str:
    isin = (trade.get("isin") or "").strip().upper()
    if isin:
        return f"isin:{isin}"
    return f"{trade['platform'].lower()}:{trade.get('productId') or trade['symbol']}"
```

- Usa la misma clave para DEGIRO, IBKR y las compras manuales.
- `Asset.platform` deja de ser único: guarda la plataforma en cada `Lot` y cada `Sale`, que ya tienen `source` y `platform`.
- Si dos productos con el mismo ISIN tienen distinta divisa, no pasa nada: cada lote ya trae su coste en euros.

**Caso de prueba:**

- 10 ASML compradas en DEGIRO el 05/02/2024 a 700 €.
- 10 ASML compradas en IBKR el 03/02/2025 a 800 €.
- Venta de 15 en IBKR el 06/04/2026 a 900 €.

El coste de la venta es 10 × 700 (lote de DEGIRO) + 5 × 800 (IBKR) = 11.000 €. Con colas separadas por bróker saldría 15 × 800 = 12.000 € (y faltarían 5 acciones).

---

## 3. Compensación de pérdidas (art. 49 LIRPF): límite del 25 % y 4 años

**Plout hoy.** Calcula la base del ahorro sumando ganancias y dividendos del año. No tiene en cuenta:

- los saldos negativos de años anteriores;
- el límite cruzado entre bloques;
- la caducidad de las pérdidas.

**La norma:**

1. **Dos bloques que se netean por separado:** ganancias y pérdidas, y rendimientos del capital mobiliario (dividendos e intereses).
2. **Bloque negativo contra el otro:** se compensa como máximo el **25 % del saldo positivo del otro bloque**.
3. **Lo que sobra se arrastra 4 años.** Primero se usan los saldos más antiguos, y cada año por el máximo que se pueda.
4. **Saldos antiguos:**
   - las pérdidas pendientes van primero contra su propio bloque;
   - el sobrante va contra el otro, también con el límite del 25 % (que se reparte con el del punto 2);
   - pasados 4 años, lo que quede se pierde.

**Implementación** (traducción directa de `Integration.swift` de Renta Bróker):

```python
def integrate(year, gains: D, income: D, pending: list[dict], cross=D("0.25"), years=4):
    g, i = gains, income
    cap_on_income = round2(income * cross) if income > 0 else D(0)
    cap_on_gains = round2(gains * cross) if gains > 0 else D(0)
    if g < 0 < i:
        used = min(-g, cap_on_income); g += used; i -= used; cap_on_income -= used
    elif i < 0 < g:
        used = min(-i, cap_on_gains); i += used; g -= used; cap_on_gains -= used

    applied, carried, expired = [], [], []
    for item in sorted((p for p in pending if p["year"] < year), key=lambda p: (p["year"], p["block"])):
        amount = item["amount"]
        if year - item["year"] > years:
            expired.append(item); continue
        if item["block"] == "gp":
            used = min(amount, max(g, 0)); g -= used; amount -= used
            used2 = min(amount, max(i, 0), cap_on_income); i -= used2; cap_on_income -= used2; amount -= used2
        else:
            used = min(amount, max(i, 0)); i -= used; amount -= used
            used2 = min(amount, max(g, 0), cap_on_gains); g -= used2; cap_on_gains -= used2; amount -= used2
        if amount > 0:
            (expired if year - item["year"] >= years else carried).append({**item, "amount": amount})
    if g < 0: carried.append({"year": year, "block": "gp", "amount": -g})
    if i < 0: carried.append({"year": year, "block": "rcm", "amount": -i})
    return max(g, 0), max(i, 0), applied, carried, expired
```

**De dónde salen los saldos pendientes:** de la declaración del año anterior, en el apartado «Saldos netos negativos… pendientes de compensación». Basta con que el usuario los escriba una vez por año de origen y bloque en `manual_trades.json` (o en un `pending_losses.json` nuevo).

**Casos de prueba:**

| Situación | Resultado |
|---|---|
| Ganancias −1.000, dividendos +2.000 | Se compensan 500 (25 % de 2.000). Base: ganancias 0, dividendos 1.500. Pasan 500 a pendientes de este año. |
| Ganancias +3.000, pendientes de 2022 por 4.000 | En 2026 ya pasaron 4 años: se usan 3.000 y caducan los otros 1.000. Avisar. |
| Ganancias +1.000, pendientes de 2024 (gp) 300 y de 2023 (gp) 900 | Primero 2023 (900), luego 100 de 2024. Quedan 200 de 2024 para el año siguiente. |

**En el pack:** una línea por año de origen y bloque, con lo aplicado este año. En Renta Web cada año de origen tiene su casilla (en 2025: 0439 a 0442 para ganancias y pérdidas de 2021 a 2024, y 0449 a 0452 para rendimientos del capital mobiliario).

---

## 4. Escala del ahorro por ejercicio

**Plout hoy.** `SAVINGS_BRACKETS` tiene una sola escala, la de 2025 (30 % a partir de 300.000 €), y se usa para todos los años.

**La norma.**
- **2023 y 2024** (Ley 38/2022): 19 / 21 / 23 / 27 / **28 %**.
- **Desde 2025** (Ley 7/2024): 19 / 21 / 23 / 27 / **30 %**.

Los tramos son los mismos: 6.000, 50.000, 200.000 y 300.000 €.

**Implementación.** Un diccionario `{año: escala}`. Si se pide un año que no está, **no se estima** (mejor «sin reglas para 2027» que un número equivocado). Lo mismo vale para los umbrales del 720/721 y los tipos de los convenios: guardarlos por año, cada uno con la URL de su fuente.

---

## 5. Venta sin compra conocida: no dar la cifra por buena

**Plout hoy.** `match_fifo` pone coste 0 a la parte sin lote y avisa (`lote-descubierto`). El pack sigue sacando un importe que parece definitivo.

**Por qué cambiarlo.** Con coste 0 la ganancia sale máxima. Si el usuario copia el pack sin leer el aviso, paga de más. Y declarar coste 0 sabiendo que no es real tampoco es correcto.

**Implementación:**

- Mantén el cálculo, pero marca la venta como `incomplete: True`.
- En el pack, en vez del valor de adquisición, escribe «Faltan compras de N acciones».
- Pon un aviso al principio que bloquee el apartado: «no rellenes este apartado hasta añadir la compra».
- Las instrucciones para la IA ya dicen que no rellene apartados con datos que faltan; esto les da algo concreto que detectar.

---

## 6. Importes con `Decimal` y redondeo solo al final

**Plout hoy.** Todo se calcula con `float`. `match_fifo` redondea cada lote (`round(acquisition, 2)`) y luego suma lotes ya redondeados. Con 3 o 4 lotes la suma puede quedar a un céntimo del valor que da Renta Web.

**Implementación:**

- Usa `decimal.Decimal` para importes y cantidades. Para crear un `Decimal` a partir de un float, usa `D(str(x))`, nunca `D(x)`.
- Guarda los importes de cada lote con todos los decimales.
- Redondea a céntimos solo:
  - el valor de transmisión;
  - el valor de adquisición;
  - el resultado;
  - cada dividendo (que se declara en euros y céntimos).
- Redondeo comercial: `quantize(D("0.01"), ROUND_HALF_UP)`. Python redondea por defecto con `ROUND_HALF_EVEN` ("banker's rounding"), que no es el que usa Hacienda.
- Al mostrar los pasos de cada lote (apartado 9), avisa de que se ven redondeados y de que la suma puede diferir en un céntimo del total.

De paso desaparecen `EPS` y `QTY_EPS`, y con ellos los avisos fantasma de «lote descubierto» por 0,00 €.

---

## 7. Tipo de cambio: nunca un tipo no oficial

**Plout hoy:**

- **DEGIRO** usa `totalPlusAllFeesInBaseCurrency`, que son los euros reales de la conversión. Eso **ya está bien**: es el criterio del art. 35 («importe real»), y es el que adoptó Renta Bróker tras cotejarlo con una declaración presentada.
- **Bybit, compras manuales y dividendos sin `grossEur`** pasan por `fx_to_eur` o `to_eur`. Si frankfurter falla, usan el **precio de Coinbase**, que no es un tipo oficial.

**Implementación:**

- **Plan B del BCE.** Guarda una copia local de la serie del BCE (el CSV histórico `eurofxref-hist.zip`) y úsala si frankfurter no responde. Si una fecha no tiene tipo publicado, usa el último publicado antes, como ya hace.
- **Si no hay tipo del BCE**, no conviertas: marca la operación como incompleta (apartado 5) en vez de usar Coinbase.
- **Para operaciones de bróker con conversión real**, compara con el BCE de ese día. Si se desvía más de un 5 %, probablemente el dato está mal leído: usa el BCE y avisa.

---

## 8. Regla de recompra: un año para valores no cotizados

**Plout hoy.** `flag_two_month_rule` usa siempre 2 meses.

**La norma (art. 33.5 f y g LIRPF).** Son 2 meses para valores admitidos a negociación y **1 año** para los no admitidos (participaciones, acciones no cotizadas). Hoy Plout solo maneja cotizados, así que no cambia nada. Pero si algún día entra algo no cotizado, la ventana tiene que depender del tipo de activo:

```python
window_months = 2 if asset.listed else 12
```

---

## 9. Anexo de justificación por venta

**Plout hoy.** El pack tiene los importes por venta y por lote, pero no explica cómo se llegó a ellos.

**Qué añadir.** Un anexo al final del Markdown del pack, con una tabla por venta:

- **Valor de transmisión:** precio × cantidad en divisa → cambio aplicado (real del bróker o BCE con su fecha) → menos cada comisión.
- **Valor de adquisición:** un renglón por lote FIFO («Lote 2: 5 de 10 compradas el 03/02/2025 a 800 €»), con sus comisiones proporcionales.
- **Resultado** y, si aplica, la parte aplazada por recompra y la fecha de la recompra.
- **Normativa aplicada,** citada una sola vez al principio del anexo (art. 35, art. 37.1.a y 37.2, art. 33.5.f y art. 49 LIRPF, y la guía fiscal de la CNMV). No hace falta repetirla en cada venta.
- **El fichero de origen,** una vez arriba si es el mismo para todas las operaciones.

Sirve para contestar un requerimiento de Hacienda: los brókers extranjeros no informan del coste de compra, así que justificarlo es cosa tuya.

---

## 10. Casillas de Renta Web leídas del diccionario oficial de la AEAT

**Plout hoy.** `SECTIONS` va por títulos de apartado, sin números de casilla. Es prudente, pero obliga a buscar cada campo a mano.

**Qué hace Renta Bróker.** La AEAT publica cada primavera `diccionarioXSD_AAAA.properties` en
`https://sede.agenciatributaria.gob.es/static_files/Sede/Disenyo_registro/DR_100_199/diccionarioXSD_AAAA.properties`. Detalles del fichero:

- **Formato:** `CLAVE=[rutaXML][tipo][casilla][etiqueta]`, en ISO-8859-1.
- **Lo que no cambia:** la ruta XML de cada campo se mantiene de un año a otro. El número de casilla puede cambiar.
- **Ejemplo:** `…/GPAcciones/EntidadAccion/G2B_A/@valor` es «Importe global de las transmisiones», casilla 0328 en 2025.
- **Saldos pendientes:** no tienen ruta propia y se localizan por el texto de la etiqueta: «Saldos netos negativos de … de {origen}, pendientes de compensación a 1 de enero de {ejercicio}».
- **Comprobar el año:** que alguna etiqueta contenga `31/12/AAAA`.

Implementación en Python: descargar el fichero, parsear los corchetes (las etiquetas pueden contener corchetes; sepáralos por profundidad) y guardar en `SECTIONS` la ruta de cada campo en vez del número. Un apartado se marca como verificado solo si se encuentran **todos** sus campos.

---

## Orden recomendado

| # | Mejora | Esfuerzo | Impacto |
|---|---|---|---|
| 1 | Tope de la doble imposición | Bajo | Evita deducirse de más (Suiza, EE. UU. sin W-8BEN) |
| 2 | FIFO por ISIN | Bajo | Coste correcto con varios brókers o bolsas |
| 3 | Compensación de pérdidas | Medio | Base del ahorro correcta; pérdidas que no caducan sin avisar |
| 4 | Escala por año | Bajo | Estimación correcta de 2023 y 2024 |
| 5 | Ventas incompletas | Bajo | No pagar de más por un coste 0 |
| 6 | `Decimal` | Medio | Cuadrar al céntimo con Renta Web |
| 7 | BCE sin Coinbase | Bajo | Solo tipos oficiales |
| 9 | Anexo | Medio | Justificación ante Hacienda |
| 10 | Casillas del diccionario | Medio | Rellenar más rápido y sin equivocarse de campo |
| 8 | Recompra a un año | Trivial | Solo si entran no cotizados |

Los casos de prueba de los apartados 1 a 3 pueden ir a `tests/test_renta_entries.py` o a un `tests/test_savings_integration.py` nuevo.
