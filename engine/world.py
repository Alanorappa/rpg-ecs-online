# world.py
from __future__ import annotations


class World:
    """
    Gerencia entidades e seus componentes.
    Uma entidade é apenas um ID. Os componentes são associados a esses IDs.

    Índice invertido (_index): component_type → set[entity_id]
    Permite que get_entities_with itere apenas sobre o conjunto mais seletivo
    em vez de percorrer todas as entidades. Custo de escrita O(1) amortizado;
    leitura O(k) onde k = tamanho do menor conjunto de candidatos.
    """

    def __init__(self):
        self._next_entity_id = 0
        # entity_id → {TipoComponente: instancia}
        self._components: dict[int, dict] = {}
        # TipoComponente → {entity_ids que possuem esse componente}
        self._index: dict[type, set] = {}

    def create_entity(self) -> int:
        """Cria uma nova entidade e retorna seu ID."""
        entity_id = self._next_entity_id
        self._next_entity_id += 1
        self._components[entity_id] = {}
        return entity_id

    def add_component(self, entity_id: int, component) -> None:
        """Adiciona um componente a uma entidade."""
        if entity_id not in self._components:
            raise ValueError(f"Entidade {entity_id} não existe.")
        ct = type(component)
        self._components[entity_id][ct] = component
        if ct not in self._index:
            self._index[ct] = set()
        self._index[ct].add(entity_id)

    def get_component(self, entity_id: int, component_type) -> object | None:
        """Retorna um componente de uma entidade pelo seu tipo."""
        return self._components.get(entity_id, {}).get(component_type)

    def get_components_for_entity(self, entity_id: int) -> dict:
        """Retorna todos os componentes de uma entidade."""
        return self._components.get(entity_id, {})

    def get_entities_with(self, *component_types) -> list:
        """Retorna lista de (entity_id, comp1, comp2, ...) para entidades com todos os tipos.

        Usa o índice invertido para iterar apenas sobre o conjunto mais seletivo,
        evitando varrer todas as entidades do mundo.
        """
        if not component_types:
            return []

        # Encontra o menor conjunto de candidatos entre todos os tipos pedidos
        sets = [self._index.get(ct, set()) for ct in component_types]
        smallest = min(sets, key=len)
        if not smallest:
            return []

        entities_found = []
        for entity_id in smallest:
            entity_components = self._components.get(entity_id)
            if entity_components is None:
                continue
            if all(ct in entity_components for ct in component_types):
                entities_found.append((entity_id, *[entity_components[ct] for ct in component_types]))
        return entities_found

    def remove_entity(self, entity_id: int) -> None:
        """Remove uma entidade e todos os seus componentes."""
        entity_components = self._components.pop(entity_id, None)
        if entity_components is not None:
            for ct in entity_components:
                idx_set = self._index.get(ct)
                if idx_set is not None:
                    idx_set.discard(entity_id)

    def remove_component(self, entity_id: int, component_type) -> None:
        """Remove um componente de uma entidade."""
        entity_components = self._components.get(entity_id)
        if entity_components and component_type in entity_components:
            del entity_components[component_type]
            idx_set = self._index.get(component_type)
            if idx_set is not None:
                idx_set.discard(entity_id)
