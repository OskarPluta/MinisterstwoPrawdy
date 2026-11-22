import { Breadcrumbs, Icon, InfoSection, ServiceSteps, TONE_ICON } from "../design/components";
import { VERDICT_VIEW } from "../result";
import type { Verdict } from "../api";

// Same words as VERDICT_PL in verify.py.
const VERDICTS: { v: Verdict; word: string; meaning: string }[] = [
  { v: "true", word: "Potwierdzone", meaning: "Źródło wprost potwierdza twierdzenie." },
  { v: "false", word: "Fałszywe", meaning: "Źródło wprost przeczy twierdzeniu." },
  { v: "misleading", word: "Wprowadzające w błąd", meaning: "Część jest prawdą, ale całość prowadzi do błędnego wniosku." },
  { v: "insufficient", word: "Brak wystarczających dowodów", meaning: "Nie znaleźliśmy źródła, które to rozstrzyga. To nie znaczy, że wiadomość jest fałszywa." },
  { v: "unverifiable", word: "Nie podlega weryfikacji", meaning: "To opinia lub ocena, a nie fakt." },
];

export function HowPage() {
  return (
    <>
      <Breadcrumbs items={[{ label: "Jak to działa" }]} />
      <h1 className="app-h1">Dowiedz się, jak sprawdzamy informacje</h1>
      <p className="app-lead">Pokazujemy, co mówią oficjalne źródła, i zawsze dajemy Ci link, żebyś mógł to sprawdzić sam.</p>

      <InfoSection title="Jak sprawdzamy" id="jak">
        <ServiceSteps
          steps={[
            "Znajdujemy w Twojej wiadomości twierdzenia, które można sprawdzić. Opinie odkładamy na bok.",
            "Szukamy pasujących komunikatów w bazie oficjalnych źródeł.",
            "Model językowy porównuje twierdzenie z fragmentami źródeł.",
            "Program sprawdza każdy cytat. Ocena bez pasującego cytatu zmienia się na „brak wystarczających dowodów”.",
          ]}
        />
      </InfoSection>

      <InfoSection title="Co oznaczają oceny" id="oceny">
        <ul className="app-verdicts">
          {VERDICTS.map(({ v, word, meaning }) => {
            const tone = VERDICT_VIEW[v].tone;
            return (
              <li key={v}>
                <span className={`app-verdict-word app-tone-text-${tone}`}>
                  <Icon name={TONE_ICON[tone]} size={20} />
                  {word}
                </span>
                <span>{meaning}</span>
              </li>
            );
          })}
        </ul>
      </InfoSection>

      <InfoSection title="Czego nie robimy" id="granice">
        <ul>
          <li>Nie rozstrzygamy, co jest prawdą absolutną. Sprawdzamy zgodność z komunikatami instytucji.</li>
          <li>Nie oceniamy wiadomości po tym, kto ją wysłał ani gdzie ją opublikowano.</li>
          <li>Linki w wyniku pochodzą tylko z naszej bazy źródeł, nigdy z tekstu wygenerowanego przez model.</li>
        </ul>
      </InfoSection>

      <InfoSection title="Co dzieje się z Twoją wiadomością" id="prywatnosc">
        <p>
          Zapisujemy wynik sprawdzenia, żeby poprawiać usługę. Zanim zapiszemy Twoją wiadomość, ukrywamy w niej adresy e-mail, numery
          telefonów, PESEL, numery dowodów i kont bankowych.
        </p>
      </InfoSection>

      <InfoSection title="Linki, zdjęcia i pliki PDF" id="material">
        <ul>
          <li>
            Link pobieramy jeden raz, jak zwykła przeglądarka, i tylko wtedy, gdy strona na to pozwala. Nie obchodzimy zabezpieczeń ani
            logowania. Jeśli strona nas nie wpuści, powiemy Ci o tym.
          </li>
          <li>Z filmów w serwisie YouTube odczytujemy tylko tytuł, kanał, opis i datę publikacji. Nie analizujemy samego nagrania.</li>
          <li>
            Tekst ze zdjęcia odczytuje model językowy u dostawcy usługi (poza Unią Europejską). Odczyt może zawierać błędy, dlatego
            pokazujemy go obok wyniku. Przed wysłaniem usuwamy z obrazu metadane, w tym lokalizację.
          </li>
          <li>Nie sprawdzamy, czy zdjęcie było edytowane, i nie szukamy jego wcześniejszych wystąpień w internecie.</li>
          <li>
            Treść materiału to to, co sprawdzamy, a nie dowód. Ocenę zawsze opieramy na źródłach z naszej bazy. Domena strony nie
            decyduje o ocenie.
          </li>
          <li>
            Nie zapisujemy przesłanych plików ani pobranych stron. W dzienniku sprawdzeń zostaje skrót pliku (hash) i krótki fragment
            odczytanego tekstu, z ukrytymi danymi osobowymi.
          </li>
        </ul>
      </InfoSection>

      <InfoSection title="Deklaracja dostępności" id="dostepnosc">
        <p>
          Serwis można obsłużyć z klawiatury i czytnikiem ekranu. Oceny mają zawsze ikonę i słowo, a nie tylko kolor. W górnym pasku
          włączysz wersję kontrastową: żółty tekst na czarnym tle.
        </p>
        <p>To prototyp. Nie przeszedł jeszcze zewnętrznego audytu dostępności.</p>
      </InfoSection>
    </>
  );
}
