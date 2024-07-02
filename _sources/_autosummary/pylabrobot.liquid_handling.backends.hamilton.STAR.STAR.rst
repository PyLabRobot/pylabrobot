pylabrobot.liquid\_handling.backends.hamilton.STAR.STAR
=======================================================

.. currentmodule:: pylabrobot.liquid_handling.backends.hamilton.STAR

.. autoclass:: STAR

   
   
   .. rubric:: Attributes

   .. autosummary::
      :toctree: .
   
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.core_parked
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.deck
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.extended_conf
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_parked
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.module_id_length
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.num_channels
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.unsafe
   
   

   
   
   .. rubric:: Methods

   .. autosummary::
      :toctree: .
   
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.__init__
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.additional_time_stamp
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.aspirate
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.aspirate96
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.aspirate_core_96
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.aspirate_pip
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.assigned_resource_callback
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.check_fw_string_error
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.check_type_is_hhc
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.check_type_is_hhs
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.close_plate_lock
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.collapse_gripper_arm
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.configure_node_names
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.core_check_resource_exists_at_location_center
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.core_get_plate
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.core_move_picked_up_resource
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.core_move_plate_to_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.core_open_gripper
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.core_pick_up_resource
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.core_put_plate
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.core_release_picked_up_resource
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.define_tip_needle
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.deserialize
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.disable_cover_control
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.discard_tip
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.discard_tips_core96
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.dispense
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.dispense96
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.dispense_core_96
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.dispense_pip
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.drain_dual_chamber_system
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.drop_tips
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.drop_tips96
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.enable_cover_control
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.fill_selected_dual_chamber
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.get_available_devices
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.get_core
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.get_id_from_fw_response
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.get_logic_iswap_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.get_or_assign_tip_type_index
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.get_temperature_at_hhc
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.get_temperature_at_hhs
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.get_ttti
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.halt
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.initialize_auto_load
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.initialize_autoload
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.initialize_core_96_head
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.initialize_dual_pump_station_valves
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.initialize_hhc
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.initialize_hhs
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.initialize_iswap
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.initialize_pipetting_channels
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_close_gripper
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_get_plate
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_move_picked_up_resource
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_open_gripper
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_pick_up_resource
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_put_plate
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_release_picked_up_resource
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_rotate
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.list_available_devices
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.load_carrier
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.lock_cover
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_all_channels_in_z_safety
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_all_pipetting_channels_to_defined_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_auto_load_to_z_save_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_autoload_to_slot
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_channel_x
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_channel_y
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_channel_z
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_core_96_head_to_defined_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_core_96_to_safe_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_iswap_x_direction
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_iswap_y_direction
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_iswap_z_direction
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_left_x_arm_to_position_with_all_attached_components_in_z_safety_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_plate_to_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_resource
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_right_x_arm_to_position_with_all_attached_components_in_z_safety_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.occupy_and_provide_area_for_external_access
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.open_not_initialized_gripper
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.open_plate_lock
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.park_autoload
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.park_iswap
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.pick_up_tip
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.pick_up_tips
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.pick_up_tips96
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.pick_up_tips_core96
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.position_components_for_free_iswap_y_range
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.position_left_x_arm_
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.position_max_free_y_for_n
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.position_right_x_arm_
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.position_single_pipetting_channel_in_y_direction
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.position_single_pipetting_channel_in_z_direction
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.pre_initialize_instrument
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.prepare_for_manual_channel_operation
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.prepare_iswap_teaching
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.probe_z_height_using_channel
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.put_core
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.query_whether_temperature_reached_at_hhc
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.read
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.release_all_occupied_areas
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.release_occupied_area
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_additional_timestamp_data
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_auto_load_slot_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_autoload_initialization_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_core_96_head_channel_tadm_error_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_core_96_head_channel_tadm_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_core_96_head_initialization_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_cover_open
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_deck_data
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_download_date
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_eeprom_data_correctness
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_electronic_board_type
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_error_code
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_extended_configuration
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_firmware_version
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_installation_data
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_instrument_initialization_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_iswap_in_parking_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_iswap_initialization_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_iswap_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_left_x_arm_last_collision_type
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_left_x_arm_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_machine_configuration
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_master_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_maximal_ranges_of_x_drives
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_name_of_last_faulty_parameter
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_node_names
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_number_of_presence_sensors_installed
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_parameter_value
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_pip_channel_validation_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_pip_height_last_lld
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_plate_in_iswap
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_position_of_core_96_head
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_present_wrap_size_of_installed_arms
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_pump_settings
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_right_x_arm_last_collision_type
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_right_x_arm_position
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_single_carrier_presence
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_supply_voltage
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_tadm_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_technical_status_of_assemblies
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_tip_presence
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_tip_presence_in_core_96_head
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_verification_data
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_xl_channel_validation_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_y_pos_channel_n
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.request_z_pos_channel_n
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.reset_output
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.save_all_cycle_counters
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.save_download_date
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.save_pip_channel_validation_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.save_technical_status_of_assemblies
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.save_xl_channel_validation_status
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.search_for_teach_in_signal_using_pipetting_channel_n_in_x_direction
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.send_command
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.send_raw_command
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.serialize
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_barcode_type
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_carrier_monitoring
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_cover_output
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_deck
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_deck_data
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_instrument_configuration
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_loading_indicators
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_minimum_traversal_height
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_not_stop
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_single_step_mode
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_x_offset_x_axis_core_96_head
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_x_offset_x_axis_core_nano_pipettor_head
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.set_x_offset_x_axis_iswap
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.setup
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.spread_pip_channels
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.start_shaking_at_hhs
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.start_temperature_control_at_hhc
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.start_temperature_control_at_hhs
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.stop
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.stop_shaking_at_hhs
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.stop_temperature_control_at_hhc
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.stop_temperature_control_at_hhs
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.store_installation_data
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.store_verification_data
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.trigger_next_step
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.unassigned_resource_callback
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.unload_carrier
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.unlock_cover
      ~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.write
   
   