# sources.d

Dodatkowe pliki źródeł wczytywane po `../sources.yaml` (alfabetycznie, tylko `*.yaml`).
Każdy plik ma własną listę `sources:`. Kotwice YAML (np. profil gov.pl `&govpl`) działają tylko w obrębie
jednego pliku, więc plik ze źródłami gov.pl definiuje profil u siebie (skopiuj `x-govpl` z `sources.yaml`).
Nazwy źródeł muszą być unikalne we wszystkich plikach.
