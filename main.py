import requests

BASE_URL = "https://pokeapi.co/api/v2/"

ascii_art = [
    "   ,--./,-.",
    "  / #      \\",
    " |          |",
    "  \\        /",
    "   `._,._,'"
]

def get_pokemon_data(pokemon_name):
    
    #if pokemon_name.isdigit():
    url = f"{BASE_URL}pokemon/{pokemon_name.lower()}"
    response = requests.get(url)
    
    if response.status_code == 200:
        data = response.json()
        return {
            "name": data["name"],
            "id": data["id"],
            "height": data["height"],
            "weight": data["weight"],
            "types": [type_info["type"]["name"] for type_info in data["types"]],
            "abilities": [ability_info["ability"]["name"] for ability_info in data["abilities"]],
            
        }
    else:
        return None

def main():
    pokemon_name = input("Enter the name or index of a Pokémon: ")
    # Impliment search by index number
    """ if pokemon_name.isdigit():
        pokemon_name = int(pokemon_name) """
    data = get_pokemon_data(pokemon_name)
    
        # Simple Neofetch-style ASCII art + info in Python
    if data:
        # Format Pokémon data as a list of strings for display
        data_lines = [
            f"Name: {data['name']}",
            f"ID: {data['id']}",
            f"Height: {data['height']}",
            f"Weight: {data['weight']}",
            f"Types: {', '.join(data['types'])}",
            f"Abilities: {', '.join(data['abilities'])}"
        ]
        # Pad both lists to the same length
        max_lines = max(len(ascii_art), len(data_lines))
        ascii_art_padded = ascii_art + [""] * (max_lines - len(ascii_art))
        data_lines_padded = data_lines + [""] * (max_lines - len(data_lines))

        for left, right in zip(ascii_art_padded, data_lines_padded):
            print(f"{left:<15}  {right}")
        main()
    else:
        print("Pokémon not found.")
        main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting Pokédex. Goodbye!")