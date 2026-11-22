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
        <p>
          Zapis przechowujemy 30 dni, a potem usuwamy go automatycznie. Pod wynikiem możesz usunąć go od razu przyciskiem „Usuń zapis
          tego sprawdzenia”. Nikt poza osobą, która dostała wynik, nie zna jego identyfikatora, więc tylko ona może go usunąć.
        </p>
        <p>
          Wynik możesz udostępnić linkiem. Strona pod linkiem jest tylko do odczytu: pokazuje wynik zapisany na serwerze, z ukrytymi danymi
          osobowymi, i nie można jej zmienić. Link działa, dopóki istnieje zapis sprawdzenia. Możesz go wcześniej wyłączyć.
        </p>
      </InfoSection>

      <InfoSection title="Skąd mamy treści źródeł" id="licencje">
        <p>
          Przechowujemy komunikaty instytucji publicznych, dane pomiarowe i wyniki weryfikacji. Dla każdego źródła określiliśmy licencję
          albo podstawę prawną. Znajdziesz je w kolumnie „Licencja” na stronie{" "}
          <a className="mp-link" href="/zrodla">
            Źródła
          </a>
          . Z serwisów fact-checkingowych zapisujemy tylko tezę, ocenę i link, bez treści artykułów.
        </p>
        <p>
          Jeśli jesteś wydawcą i chcesz, żebyśmy usunęli Twoje materiały, napisz do zespołu. Usuwamy je z bazy, z archiwum i z zapisów
          sprawdzeń, w których były cytowane, a adres trafia do rejestru usuniętych treści, więc nie wróci przy kolejnym pobraniu.
        </p>
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
