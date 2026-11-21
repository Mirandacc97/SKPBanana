require 'sketchup.rb'
require 'json'
require 'open3'
require 'tempfile'
require 'base64'
require 'fileutils'

module NanoBananaRender
  PLUGIN_DIR = File.join(File.dirname(File.dirname(__FILE__)), 'nano_banana_render')
  CONFIG_FILE = File.join(PLUGIN_DIR, 'config.json')

  DEFAULT_CONFIG = {
    'api_key' => '',
    'model' => 'gemini-2.5-flash-image',
    'num_options' => 2,
    'last_prompt' => ''
  }.freeze

  @config = nil
  @dialog = nil
  @results_dialog = nil

  class << self
    def config
      @config ||= load_config
    end

    def load_config
      if File.exist?(CONFIG_FILE)
        DEFAULT_CONFIG.merge(JSON.parse(File.read(CONFIG_FILE)))
      else
        DEFAULT_CONFIG.dup
      end
    rescue StandardError
      DEFAULT_CONFIG.dup
    end

    def save_config
      FileUtils.mkdir_p(File.dirname(CONFIG_FILE))
      File.write(CONFIG_FILE, JSON.pretty_generate(config.reject { |k, _| k == 'api_key' }))
    end

    def capture_view(filepath)
      view = Sketchup.active_model.active_view
      keys = {
        filename: filepath,
        width: view.vpwidth * 2,
        height: view.vpheight * 2,
        antialias: true,
        transparent: false
      }
      raise 'Viewport capture failed' unless view.write_image(keys)
      filepath
    end

    def run_worker(dialog, job, capture: nil, &on_success)
      python = ENV.fetch('SKPBANANA_PYTHON', 'python')
      task = Thread.new do
        begin
          output, status = Open3.capture2(python, File.join(PLUGIN_DIR, 'worker.py'),
                                          stdin_data: JSON.generate(job), binmode: true)
          raise 'Python worker failed' unless status.success?
          JSON.parse(output)
        rescue Errno::ENOENT
          { 'error' => 'Python unavailable. Set SKPBANANA_PYTHON to the executable path.' }
        rescue StandardError
          { 'error' => 'Could not read the Python worker response.' }
        ensure
          capture.close! if capture
        end
      end

      timer = UI.start_timer(0.15, true) do
        next if task.alive?
        UI.stop_timer(timer)
        next unless dialog.visible?
        reply = task.value
        dialog.execute_script("setLoading(false)")
        if reply['error']
          dialog.execute_script("showStatus(#{JSON.generate(reply['error'])}, 'error')")
        else
          on_success.call(reply.fetch('value'))
        end
      end
    end

    def show_prompt_dialog
      if @dialog && @dialog.visible?
        @dialog.bring_to_front
        return
      end

      html_path = File.join(PLUGIN_DIR, 'html', 'prompt_dialog.html')

      @dialog = UI::HtmlDialog.new({
        dialog_title: 'Nano Banana Pro Render',
        width: 520,
        height: 620,
        resizable: true,
        style: UI::HtmlDialog::STYLE_DIALOG
      })

      @dialog.add_action_callback('get_config') do |_ctx|
        @dialog.execute_script("setConfig(#{JSON.generate(config)})")
      end

      @dialog.add_action_callback('save_api_key') do |_ctx, key|
        config['api_key'] = key.to_s.strip
        save_config
        @dialog.execute_script("showStatus('API key set for this session', 'success')")
      end

      @dialog.add_action_callback('enhance_prompt') do |_ctx, prompt|
        if config['api_key'].empty?
          @dialog.execute_script("showStatus('Set your API key first', 'error')")
          next
        end

        dialog = @dialog
        job = { action: 'enhance', key: config['api_key'], prompt: prompt }
        dialog.execute_script("setLoading(true)")
        run_worker(dialog, job) do |enhanced|
          dialog.execute_script("setEnhancedPrompt(#{JSON.generate(enhanced)})")
        end
      end

      @dialog.add_action_callback('render') do |_ctx, prompt, num_options, model_name|
        if config['api_key'].empty?
          @dialog.execute_script("showStatus('Set your API key first', 'error')")
          next
        end

        num = num_options.to_i
        num = 1 if num < 1
        num = 4 if num > 4
        config['num_options'] = num
        config['last_prompt'] = prompt
        config['model'] = model_name.to_s if model_name && !model_name.to_s.empty?
        save_config

        @dialog.execute_script("setLoading(true)")

        begin
          capture = Tempfile.new(['skpbanana-', '.png'])
          capture.close
          capture_view(capture.path)
          job = { action: 'render', key: config['api_key'], model: config['model'],
                  capture: capture.path, prompt: prompt, count: num }
          run_worker(@dialog, job, capture: capture) do |result|
            show_results_dialog(JSON.generate(result))
          end
        rescue StandardError => e
          capture.close! if capture
          @dialog.execute_script("setLoading(false)")
          @dialog.execute_script("showStatus(#{JSON.generate(e.message)}, 'error')")
        end
      end

      @dialog.set_file(html_path)
      @dialog.show
    end

    def show_results_dialog(payload_json)
      if @results_dialog && @results_dialog.visible?
        @results_dialog.close
      end

      html_path = File.join(PLUGIN_DIR, 'html', 'results_dialog.html')

      @results_dialog = UI::HtmlDialog.new({
        dialog_title: 'Render Results - Nano Banana Pro',
        width: 1000,
        height: 750,
        resizable: true,
        style: UI::HtmlDialog::STYLE_DIALOG
      })

      @results_dialog.add_action_callback('get_results') do |_ctx|
        @results_dialog.execute_script("loadResults(#{payload_json})")
      end

      @results_dialog.add_action_callback('save_image') do |_ctx, base64_data, mime|
        ext = { 'image/jpeg' => '.jpg', 'image/webp' => '.webp' }.fetch(mime, '.png')
        path = UI.savepanel('Save Rendered Image', '', "render#{ext}")
        if path
          path += ext unless path.downcase.end_with?(ext)
          File.binwrite(path, Base64.decode64(base64_data))
          UI.messagebox("Image saved to:\n#{path}")
        end
      end

      @results_dialog.set_file(html_path)
      @results_dialog.show
    end

    def setup_menu
      menu = UI.menu('Plugins')
      submenu = menu.add_submenu('Nano Banana Pro Render')

      submenu.add_item('Render Current View') { show_prompt_dialog }
      submenu.add_item('Settings') { show_prompt_dialog }

      toolbar = UI::Toolbar.new('Nano Banana Pro')
      cmd = UI::Command.new('Render') { show_prompt_dialog }
      cmd.tooltip = 'Render with Nano Banana Pro'
      cmd.status_bar_text = 'Capture view and render with AI'

      icon_path = File.join(PLUGIN_DIR, 'icons', 'render')
      cmd.small_icon = "#{icon_path}_24.png" if File.exist?("#{icon_path}_24.png")
      cmd.large_icon = "#{icon_path}_32.png" if File.exist?("#{icon_path}_32.png")

      toolbar.add_item(cmd)
      toolbar.show
    end
  end

  setup_menu

end
