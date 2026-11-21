# SKPBanana para SketchUp

Capture a vista atual, edite a imagem por IA e compare as variações no painel. O Ruby acessa a câmera do SketchUp e o Python executa o provedor escolhido.

## Installation

1. Install Python 3.10+ and make `python` available to SketchUp, or set `SKPBANANA_PYTHON` to its full executable path before starting SketchUp. The Python worker uses only the standard library. Download or clone this repository.
2. Copy `nano_banana_render.rb` and the `nano_banana_render/` folder into your SketchUp Plugins directory:
   - **Windows:** `C:\Users\<YOU>\AppData\Roaming\SketchUp\SketchUp 2024\SketchUp\Plugins\`
   - **macOS:** `~/Library/Application Support/SketchUp 2024/SketchUp/Plugins/`
3. Restart SketchUp
4. The plugin appears under **Plugins > Nano Banana Pro Render** and in the toolbar

## Usar o ChatGPT pelo Codex

1. Instale o Codex CLI na máquina da designer e execute `codex login` com a conta ChatGPT dela. Confira com `codex login status`.
2. Abra **Plugins → Nano Banana Pro Render → Render Current View**. O provedor padrão é **ChatGPT via Codex CLI**.
3. Escreva o pedido de edição, escolha uma variação para o primeiro teste e clique em **Capture View & Render**. O worker anexa o PNG ao CLI e recupera o arquivo gerado na pasta da sessão. Cada variação usa a quota da conta.
4. Compare o resultado com a captura e salve a imagem pelo painel.

### Modelos e preferências

Edite [render.conf](nano_banana_render/render.conf) dentro da pasta instalada da extensão. Feche e reabra o painel para recarregar os valores padrão. No Windows, o SketchUp precisa encontrar `python.exe` e `codex.exe` no próprio ambiente.

| Parâmetro | Uso |
|---|---|
| `provider` | Provedor inicial do painel. Aceita `codex`, `openai` ou `gemini`. A escolha no painel vale para aquela operação. |
| `agent_model` | Modelo do agente em `codex exec -m`. Deixe vazio para usar a seleção automática ou informe um modelo disponível na conta, por exemplo `gpt-6.2-sol`. |
| `codex_executable` | Nome no PATH ou caminho completo ao executável nativo, sem aspas. |
| `image_model` | Modelo para edição pela **OpenAI Images API**, por padrão `chatgpt-image-latest`. |

No modo Codex, o agente aciona a ferramenta nativa de imagens do ChatGPT. O CLI determina o modelo interno dessa ferramenta. O modo **OpenAI Images API** permite escolher explicitamente o modelo de imagem e exige `OPENAI_API_KEY` no ambiente do SketchUp, com cobrança da conta de API. A extensão só usa esse serviço quando selecionado. O botão **Enhance Prompt** usa o Codex nos dois modos OpenAI.

O Gemini continua disponível no seletor e usa a chave Google informada no painel para a sessão. Cada variação solicita uma edição ao provedor selecionado. A edição pode alterar detalhes da geometria aparente. Revise a captura e a versão gerada antes de usar o resultado.

[Modo não interativo do Codex](https://developers.openai.com/codex/noninteractive) · [Edição de imagens da OpenAI](https://developers.openai.com/api/docs/guides/image-generation)

## Verificação

Execute `python -m unittest discover -s tests -v` na raiz do repositório. A homologação no SketchUp 2024 requer instalar o pacote, abrir uma cena, gerar uma variação e salvar o arquivo.
